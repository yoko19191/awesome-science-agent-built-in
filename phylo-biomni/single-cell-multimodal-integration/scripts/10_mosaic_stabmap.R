#!/usr/bin/env Rscript
# Explicit unsupported adapter for mosaic StabMap requests.
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages(source(file.path(here, "_common.R")))

cfg <- load_config()
ensure_dirs(cfg)
write_status(cfg, "stabmap_status.json", list(
  ran = FALSE,
  method = "StabMap",
  capability_status = "unsupported",
  reason = paste(
    "unsupported: no packaged shared-feature bridge, StabMap model fit,",
    "or genuine label-transfer implementation"
  )
))
quit(status = 2)
