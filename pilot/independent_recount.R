# Conferência independente dos números do piloto DDPA (PRD_reescrita_manuscrito_JPI_20260922, sequência 1).
# Implementação separada (R, sem reaproveitar o código Python): lê as saídas por campo guardadas localmente,
# refaz a regra de resolução a partir das leituras do detector (l1) e do modelo (l2), recalcula contagens e IC exato
# (binom.test) e compara com _piloto_resultados_NO_PHI.json e _piloto_escalonamento_NO_PHI.json.
# A âncora textual do Ki-67 é refeita com expressão regular própria sobre o texto de IHQ de cada caso (arquivo local).
# Saída: _conferencia_independente_piloto_NO_PHI.json (só agregados). Nada sai da máquina.
suppressWarnings(suppressMessages({ library(jsonlite) }))

AQUI  <- "<local path>"
V1    <- "<local path>"
SAI   <- file.path(V1, "_piloto_saidas")
F38   <- "<local path>"

ler <- function(f) {
  d <- read.csv(file.path(SAI, f), colClasses = "character", na.strings = character(0), encoding = "UTF-8")
  d[] <- lapply(d, function(x) { x <- trimws(x); x[x %in% c("", "NA", "nan", "None")] <- NA; x })
  d
}
num_ki <- function(x) { v <- suppressWarnings(as.numeric(x)); ifelse(is.na(v), NA, as.character(round(v))) }

# âncora textual própria: "ki" + "67" e, em até 80 caracteres sem '%', um número seguido de '%'
ancora_ki67 <- function(texto, valor) {
  if (is.na(texto) || is.na(valor)) return(FALSE)
  t <- tolower(texto)
  pos <- gregexpr("ki\\s*-?\\s*67", t, perl = TRUE)[[1]]
  if (pos[1] == -1) return(FALSE)
  for (p in pos) {
    trecho <- substr(t, p, p + 120)
    m <- regmatches(trecho, regexec("ki\\s*-?\\s*67[^%]{0,80}?(\\d{1,3}(?:[.,]\\d+)?)\\s*%", trecho, perl = TRUE))[[1]]
    if (length(m) == 2) {
      v <- round(as.numeric(sub(",", ".", m[2])))
      if (!is.na(v) && as.character(v) == valor) return(TRUE)
    }
  }
  FALSE
}

ic <- function(k, n) if (n == 0) NA else round(100 * binom.test(k, n)$conf.int[1:2], 1)
disc <- function(val, ref, sel) {
  m <- sel & !is.na(val)
  k <- sum(m & (is.na(ref) | val != ref))
  list(n = sum(m), discordantes = k, pct = if (sum(m)) round(100 * k / sum(m), 1) else NA, ic95 = ic(k, sum(m)))
}

textos_breast_study <- NULL
if (file.exists(F38)) {
  casos <- fromJSON(F38, simplifyVector = FALSE)
  textos_breast_study <- setNames(vapply(casos, function(c) {
    seg <- Filter(function(s) identical(s$kind, "ihq"), c$segments)
    paste(vapply(seg, function(s) s$text, ""), collapse = "\n\n")
  }, ""), vapply(casos, function(c) c$case_id, ""))
}

conta <- function(d, var, conj) {
  ref <- d[[paste0("ref_", var)]]; a <- d[[paste0("l1_", var)]]; b <- d[[paste0("l2_", var)]]
  if (var == "ki67") { ref <- num_ki(ref); a <- num_ki(a); b <- num_ki(b) }
  estado <- character(nrow(d)); valor <- rep(NA_character_, nrow(d)); comp <- character(nrow(d))
  for (i in seq_len(nrow(d))) {
    if (!is.na(a[i]) && !is.na(b[i])) {
      if (a[i] == b[i]) { estado[i] <- "aceito_concordancia"; valor[i] <- a[i] } else { estado[i] <- "escalado"; comp[i] <- "discordancia_real" }
    } else if (!is.na(a[i])) { estado[i] <- "escalado"; comp[i] <- "modelo_absteve"
    } else if (!is.na(b[i])) {
      anc <- var == "ki67" && !is.null(textos_breast_study) && ancora_ki67(textos_breast_study[d$caso[i]], b[i])
      if (anc) { estado[i] <- "aceito_suporte_textual"; valor[i] <- b[i] } else { estado[i] <- "escalado"; comp[i] <- "detector_absteve" }
    } else estado[i] <- "sem_resposta"
  }
  guardado <- d[[paste0("estado_", var)]]
  hib <- ifelse(!is.na(b), b, a)
  so_mod_err <- !is.na(b) & (is.na(ref) | b != ref)
  list(
    variavel = var, n = nrow(d),
    estados_iguais_ao_guardado = sum(estado == guardado), estados_diferentes_do_guardado = sum(estado != guardado),
    aceitos_por_concordancia = sum(estado == "aceito_concordancia"),
    disc_aceitos = disc(valor, ref, estado == "aceito_concordancia"),
    aceitos_por_suporte_textual = sum(estado == "aceito_suporte_textual"),
    disc_suporte = disc(valor, ref, estado == "aceito_suporte_textual"),
    escalados = sum(estado == "escalado"), sem_resposta = sum(estado == "sem_resposta"),
    composicao = as.list(table(factor(comp[estado == "escalado"], levels = c("discordancia_real", "detector_absteve", "modelo_absteve")))),
    so_detector = c(list(cobertura = sum(!is.na(a))), disc(a, ref, rep(TRUE, nrow(d)))),
    so_modelo = c(list(cobertura = sum(!is.na(b))), disc(b, ref, rep(TRUE, nrow(d)))),
    hibrido = c(list(cobertura = sum(!is.na(hib))), disc(hib, ref, rep(TRUE, nrow(d)))),
    erros_modelo_sozinho = sum(so_mod_err), desses_no_escalonamento = sum(so_mod_err & estado == "escalado"),
    escalados_modelo_igual_referencia = sum(estado == "escalado" & !is.na(b) & !is.na(ref) & b == ref),
    escalados_detector_igual_referencia = sum(estado == "escalado" & !is.na(a) & !is.na(ref) & a == ref)
  )
}

res <- list(
  cervical = { d <- ler("campos_cervical_todos_LOCAL.csv"); list(resultado = conta(d, "resultado"), adequab = conta(d, "adequab")) },
  tireoide_A = { d <- ler("campos_tireoide_braco_A_sorteio_LOCAL.csv"); list(bethesda = conta(d, "bethesda")) },
  tireoide_B1 = { d <- ler("campos_tireoide_braco_B1_enriquecido_LOCAL.csv"); list(bethesda = conta(d, "bethesda")) },
  mama_ihq = { d <- ler("campos_mama_ihq_todos_LOCAL.csv"); list(re = conta(d, "re"), rp = conta(d, "rp"), her2 = conta(d, "her2"), ki67 = conta(d, "ki67")) }
)

# comparação com os agregados publicados pelo script original
orig <- fromJSON(file.path(V1, "_piloto_resultados_NO_PHI.json"), simplifyVector = FALSE)
esc  <- fromJSON(file.path(V1, "_piloto_escalonamento_NO_PHI.json"), simplifyVector = FALSE)
mapa <- list(
  list("cervical", "resultado", orig$conjuntos$cervical$resultados$todos[[1]], esc[["cervical_todos|resultado"]]),
  list("cervical", "adequab", orig$conjuntos$cervical$resultados$todos[[2]], esc[["cervical_todos|adequab"]]),
  list("tireoide_A", "bethesda", orig$conjuntos$tireoide$resultados$braco_A_sorteio[[1]], esc[["tireoide_braco_A_sorteio|bethesda"]]),
  list("tireoide_B1", "bethesda", orig$conjuntos$tireoide$resultados$braco_B1_enriquecido[[1]], esc[["tireoide_braco_B1_enriquecido|bethesda"]]),
  list("mama_ihq", "re", orig$conjuntos$mama_ihq$resultados$todos[[1]], esc[["mama_ihq_todos|re"]]),
  list("mama_ihq", "rp", orig$conjuntos$mama_ihq$resultados$todos[[2]], esc[["mama_ihq_todos|rp"]]),
  list("mama_ihq", "her2", orig$conjuntos$mama_ihq$resultados$todos[[3]], esc[["mama_ihq_todos|her2"]]),
  list("mama_ihq", "ki67", orig$conjuntos$mama_ihq$resultados$todos[[4]], esc[["mama_ihq_todos|ki67"]])
)
difs <- list()
cmp <- function(rotulo, meu, deles) if (!isTRUE(all.equal(unlist(meu), unlist(deles)))) difs[[length(difs) + 1]] <<- list(campo = rotulo, independente = meu, original = deles)
for (m in mapa) {
  r <- res[[m[[1]]]][[m[[2]]]]; o <- m[[3]]; e <- m[[4]]; tag <- paste(m[[1]], m[[2]])
  cmp(paste(tag, "n"), r$n, o$n)
  cmp(paste(tag, "aceitos"), r$aceitos_por_concordancia, o$DDPA$aceitos_por_concordancia)
  cmp(paste(tag, "disc_aceitos"), r$disc_aceitos[c("n", "discordantes")], o$DDPA$discordancia_nos_aceitos_por_concordancia[c("n", "discordantes")])
  cmp(paste(tag, "ic_aceitos"), r$disc_aceitos$ic95, o$DDPA$discordancia_nos_aceitos_por_concordancia$ic95)
  cmp(paste(tag, "suporte"), r$aceitos_por_suporte_textual, o$DDPA$aceitos_por_suporte_textual)
  cmp(paste(tag, "disc_suporte"), r$disc_suporte[c("n", "discordantes")], o$DDPA$discordancia_nos_aceitos_por_suporte[c("n", "discordantes")])
  cmp(paste(tag, "escalados"), r$escalados, o$DDPA$escalados)
  cmp(paste(tag, "sem_resposta"), r$sem_resposta, o$DDPA$sem_resposta)
  cmp(paste(tag, "so_detector"), r$so_detector[c("cobertura", "n", "discordantes")], c(list(cobertura = o$so_detector$cobertura), o$so_detector$discordancia[c("n", "discordantes")]))
  cmp(paste(tag, "so_modelo"), r$so_modelo[c("cobertura", "n", "discordantes")], c(list(cobertura = o$so_modelo$cobertura), o$so_modelo$discordancia[c("n", "discordantes")]))
  cmp(paste(tag, "hibrido"), r$hibrido[c("cobertura", "n", "discordantes")], c(list(cobertura = o$concordou_aceita_senao_modelo$cobertura), o$concordou_aceita_senao_modelo$discordancia[c("n", "discordantes")]))
  cmp(paste(tag, "ic_so_detector"), r$so_detector$ic95, o$so_detector$discordancia$ic95)
  cmp(paste(tag, "ic_so_modelo"), r$so_modelo$ic95, o$so_modelo$discordancia$ic95)
  cmp(paste(tag, "ic_hibrido"), r$hibrido$ic95, o$concordou_aceita_senao_modelo$discordancia$ic95)
  cmp(paste(tag, "composicao"), r$composicao, e$composicao)
  cmp(paste(tag, "erros_modelo"), c(r$erros_modelo_sozinho, r$desses_no_escalonamento), c(e$erros_modelo_sozinho, e$desses_no_escalonamento))
  cmp(paste(tag, "escalados_certos"), c(r$escalados_modelo_igual_referencia, r$escalados_detector_igual_referencia), c(o$escalados_em_que_o_modelo_estava_certo, o$escalados_em_que_o_detector_estava_certo))
}
saida <- list(
  o_que_e = "Recontagem independente (R) dos números do piloto DDPA a partir das saídas por campo; comparação com os agregados do script original.",
  ancora_ki67_recalculada = !is.null(textos_breast_study),
  resultados = res,
  divergencias_com_o_original = difs,
  veredito = if (length(difs) == 0) "CONFERE: nenhuma divergência" else paste(length(difs), "divergência(s)")
)
write_json(saida, file.path(AQUI, "_conferencia_independente_piloto_NO_PHI.json"), auto_unbox = TRUE, pretty = TRUE, na = "null")
cat(saida$veredito, "\n")
for (x in difs) cat(" -", x$campo, ": independente", toJSON(x$independente, auto_unbox = TRUE), "| original", toJSON(x$original, auto_unbox = TRUE), "\n")
