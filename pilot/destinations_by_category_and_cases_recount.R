# Recontagem independente em R dos complementos do PRD v6 (destinos por categoria, sem resposta × anotação, casos de mama, Ki-67 aceitos).
suppressWarnings(suppressMessages(library(jsonlite)))
SAI <- "<local path>"
OUT <- "<local path>"
ler <- function(f) { d <- read.csv(file.path(SAI, f), colClasses = "character", na.strings = character(0), encoding = "UTF-8")
  d[] <- lapply(d, function(x) { x <- trimws(x); x[x %in% c("", "NA", "nan", "None")] <- NA; x }); d }
canon <- function(x, var) { if (var == "ki67") { v <- suppressWarnings(as.numeric(x)); return(ifelse(is.na(v), NA, as.character(round(v)))) }; x }
conj <- list(list("cervical", "campos_cervical_todos_LOCAL.csv", c("resultado", "adequab")),
             list("tireoide_A", "campos_tireoide_braco_A_sorteio_LOCAL.csv", "bethesda"),
             list("tireoide_B1", "campos_tireoide_braco_B1_enriquecido_LOCAL.csv", "bethesda"),
             list("mama_ihq", "campos_mama_ihq_todos_LOCAL.csv", c("re", "rp", "her2", "ki67")))
res <- list(destino_por_categoria_anotada = list(), sem_resposta_por_anotacao = list(), mama_casos = list(), ki67_todos_os_aceitos = list())
for (cj in conj) { d <- ler(cj[[2]])
  for (var in cj[[3]]) {
    ref <- canon(d[[paste0("ref_", var)]], var); est <- d[[paste0("estado_", var)]]; val <- canon(d[[paste0("ddpa_", var)]], var)
    aceito <- est %in% c("aceito_concordancia", "aceito_suporte_textual"); sr <- est == "sem_resposta"
    res$sem_resposta_por_anotacao[[paste0(cj[[1]], "|", var)]] <- list(sem_resposta = sum(sr), com_valor_anotado = sum(sr & !is.na(ref)), anotacao_nao_informa = sum(sr & is.na(ref)))
    if (var != "ki67") { refc <- ifelse(is.na(ref), "NAO_INFORMA", ref); tab <- list()
      for (cat in sort(unique(refc))) { m <- refc == cat
        igual <- m & aceito & !is.na(val) & !is.na(ref) & val == ref
        tab[[cat]] <- list(anotados = sum(m), aceito_igual = sum(igual), aceito_diferente = sum(m & aceito & !igual),
                           encaminhado = sum(m & est == "escalado"), sem_resposta = sum(m & sr)) }
      res$destino_por_categoria_anotada[[paste0(cj[[1]], "|", var)]] <- tab
    } else { igual <- aceito & !is.na(val) & !is.na(ref) & val == ref; k <- sum(aceito & !igual); n <- sum(aceito)
      res$ki67_todos_os_aceitos <- list(aceitos = n, discordantes = k, pct = round(100 * k / n, 1), ic95 = round(100 * as.numeric(binom.test(k, n)$conf.int), 1),
                                        por_concordancia = sum(est == "aceito_concordancia"), por_suporte_textual = sum(est == "aceito_suporte_textual")) } }
  if (cj[[1]] == "mama_ihq") { enc <- Reduce(`+`, lapply(cj[[3]], function(v) as.integer(d[[paste0("estado_", v)]] == "escalado")))
    srq <- Reduce(`+`, lapply(cj[[3]], function(v) as.integer(d[[paste0("estado_", v)]] == "sem_resposta")))
    tb <- table(enc); res$mama_casos <- list(casos = nrow(d), casos_com_algum_campo_encaminhado = sum(enc > 0), campos_encaminhados_total = sum(enc),
      distribuicao_campos_encaminhados_por_caso = as.list(setNames(as.integer(tb), names(tb))), casos_com_algum_campo_sem_resposta = sum(srq > 0)) } }
write_json(res, OUT, auto_unbox = TRUE, pretty = TRUE)
cat("R ok\n")
