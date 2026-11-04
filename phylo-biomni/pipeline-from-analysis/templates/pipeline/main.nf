#!/usr/bin/env nextflow
include { PIPELINE } from './workflows/pipeline'

workflow {
    if (!params.input) {
        error "Provide --input <samplesheet.csv>"
    }
    PIPELINE(params.input)
}
