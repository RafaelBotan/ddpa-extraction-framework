# Recontagem independente (R, implementação separada) da análise condicional do PRD v6: discordância do modelo por estado
# do detector e do detector por estado do modelo. Lê as mesmas saídas por campo locais; grava só agregados.
suppressWarnings(suppressMessages(library(jsonlite)))
SAI <- "<local path>"
OUT <- "<local path>"
ler <- function(f) {
  d <- read.csv(file.path(SAI, f), colClasses = "character", na.strings = character(0), encoding = "UTF-8")
  d[] <- lapply(d, function(x) { x <- trimws(x); x[x %in% c("", "NA", "nan", "None")] <- NA; x })
  d
}
canon <- function(x, var) {
  if (var == "ki67") { v <- suppressWarnings(as.numeric(x)); return(ifelse(is.na(v), NA, as.character(round(v)))) }
  x
}
cel <- function(k, n) {
  if (n == 0) return(list(n = 0L, discordantes = 0L, pct = NULL, ic95 = NULL))
  ci <- binom.test(k, n)$conf.int
  list(n = n, discordantes = k, pct = round(100 * k / n, 1), ic95 = round(100 * as.numeric(ci), 1))
}
conjuntos <- list(
  list("cervical", "campos_cervical_todos_LOCAL.csv", c("resultado", "adequab")),
  list("tireoide_A", "campos_tireoide_braco_A_sorteio_LOCAL.csv", c("bethesda")),
  list("tireoide_B1", "campos_tireoide_braco_B1_enriquecido_LOCAL.csv", c("bethesda")),
  list("mama_ihq", "campos_mama_ihq_todos_LOCAL.csv", c("re", "rp", "her2", "ki67")))
res <- list()
for (cj in conjuntos) {
  d <- ler(cj[[2]]); res[[cj[[1]]]] <- list()
  for (var in cj[[3]]) {
    ref <- canon(d[[paste0("ref_", var)]], var); l1 <- canon(d[[paste0("l1_", var)]], var); l2 <- canon(d[[paste0("l2_", var)]], var)
    agree <- !is.na(l1) & !is.na(l2) & l1 == l2
    disagree <- !is.na(l1) & !is.na(l2) & l1 != l2
    det_sil <- is.na(l1) & !is.na(l2); mod_sil <- is.na(l2) & !is.na(l1); both <- is.na(l1) & is.na(l2)
    m_disc <- !is.na(l2) & (is.na(ref) | l2 != ref)
    d_disc <- !is.na(l1) & (is.na(ref) | l1 != ref)
    res[[cj[[1]]]][[var]] <- list(
      modelo_por_estado_do_detector = list(
        detector_concorda = cel(sum(agree & m_disc), sum(agree)),
        detector_discorda = cel(sum(disagree & m_disc), sum(disagree)),
        detector_calado = cel(sum(det_sil & m_disc), sum(det_sil))),
      detector_por_estado_do_modelo = list(
        modelo_concorda = cel(sum(agree & d_disc), sum(agree)),
        modelo_discorda = cel(sum(disagree & d_disc), sum(disagree)),
        modelo_calado = cel(sum(mod_sil & d_disc), sum(mod_sil))),
      ambos_calados = sum(both),
      em_discordancia_quem_bate_com_a_anotacao = list(
        so_o_modelo = sum(disagree & !m_disc & d_disc), so_o_detector = sum(disagree & m_disc & !d_disc), nenhum = sum(disagree & m_disc & d_disc)))
  }
}
write_json(res, OUT, auto_unbox = TRUE, pretty = TRUE, null = "null")
cat("R: gravado", OUT, "\n")
