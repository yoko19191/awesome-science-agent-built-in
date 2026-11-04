// Parses a CSV samplesheet with columns sample,fastq_1,fastq_2 (fastq_2 empty for single-end).
// Adapt the columns when the analysis starts from other inputs (counts, BAMs, h5ad).
workflow SAMPLESHEET_FASTQ {
    take:
    samplesheet

    main:
    reads = channel.fromPath(samplesheet, checkIfExists: true)
        .splitCsv(header: true, strip: true)
        .map { row ->
            if (!row.sample || !row.fastq_1) {
                error "samplesheet rows need sample and fastq_1: ${row}"
            }
            def single = !row.fastq_2
            def meta = [id: row.sample, single_end: single]
            def files = single ? [file(row.fastq_1, checkIfExists: true)]
                               : [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)]
            [meta, files]
        }

    emit:
    reads
}
