// nextflow-skills: versions-collector
// Template-owned: keep it exactly as shipped. It consumes the versions topic, so it must not
// emit to it, and a meta input or topic output would deadlock the run (the topic never closes).
process SOFTWARE_VERSIONS {
    tag "versions"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container 'quay.io/biocontainers/python:3.12'

    input:
    path versions

    output:
    path "software_versions.tsv", emit: tsv

    when:
    task.ext.when == null || task.ext.when

    script:
    """
    (printf 'process\\ttool\\tversion\\n'; sort -u ${versions}) > software_versions.tsv
    """

    stub:
    """
    touch software_versions.tsv
    """
}
