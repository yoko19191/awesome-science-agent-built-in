include { SAMPLESHEET_FASTQ } from '../subworkflows/local/samplesheet_fastq'
include { SOFTWARE_VERSIONS } from '../modules/local/software_versions'
// Components from the approved plan are included here, e.g.
// include { FASTP } from '../modules/nf-core/fastp/main'

workflow PIPELINE {
    take:
    samplesheet

    main:
    SAMPLESHEET_FASTQ(samplesheet)
    ch_reads = SAMPLESHEET_FASTQ.out.reads

    // Steps from the approved plan are wired here, in plan order.

    // Keep as shipped: the collector reads the versions topic and must not emit to it.
    SOFTWARE_VERSIONS(
        channel.topic('versions')
            .map { process, tool, version -> "${process.tokenize(':')[-1]}\t${tool}\t${version}" }
            .unique()
            .collectFile(name: 'versions.tsv', newLine: true, sort: true)
    )
}
