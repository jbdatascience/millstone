"""
Functions for calling Variants.
"""

import os
import re
import subprocess
import vcf

from celery import task

from pipeline.read_alignment import get_insert_size
from pipeline.read_alignment import get_read_length
from main.models import AlignmentGroup
from main.models import Dataset
from main.models import ensure_exists_0775_dir
from main.model_utils import clean_filesystem_location
from main.model_utils import get_dataset_with_type
from read_alignment import get_discordant_read_pairs
from read_alignment import get_insert_size
from read_alignment import get_split_reads
from main.s3 import project_files_needed
from utils.jbrowse_util import add_vcf_track
from utils import uppercase_underscore
from settings import TOOLS_DIR
from variants.vcf_parser import parse_alignment_group_vcf
from variants.variant_sets import add_variants_to_set_from_bed
from variant_effects import run_snpeff

# TODO: These VCF types should be set somewhere else. snpeff_util and
# vcf_parser also use them, but where should they go? settings.py seems
# logical, but it cannot import from models.py... -dbg

# Dataset type to use for snp calling.
VCF_DATASET_TYPE = Dataset.TYPE.VCF_FREEBAYES
# Dataset type to use for snp annotation.
VCF_ANNOTATED_DATASET_TYPE = Dataset.TYPE.VCF_FREEBAYES_SNPEFF

# Dataset type for results of finding SVs.
VCF_PINDEL_TYPE = Dataset.TYPE.VCF_PINDEL
VCF_DELLY_TYPE = Dataset.TYPE.VCF_DELLY

# Returns a dictionary of common parameters required for all variant callers
# (freebayes, pindel, delly)
def get_common_tool_params(alignment_group):
    alignment_type = Dataset.TYPE.BWA_ALIGN
    return {
            'alignment_group': alignment_group,
            'alignment_type': alignment_type,
            'fasta_ref': _get_fasta_ref(alignment_group),
            'output_dir': _create_output_dir(alignment_group),
            'sample_alignments': _find_valid_sample_alignments(
                    alignment_group, alignment_type),
            }


def get_variant_tool_params():
    """Returns a tuple of variant tools params to pass into
    find_variants_with_tool.
    """
    return (
            ('freebayes', Dataset.TYPE.VCF_FREEBAYES, run_freebayes),
            ('pindel', Dataset.TYPE.VCF_PINDEL, run_pindel),
            ('delly', Dataset.TYPE.VCF_DELLY, run_delly),
            ('lumpy', Dataset.TYPE.VCF_LUMPY, run_lumpy),
    )


def _get_fasta_ref(alignment_group):
    # Grab the reference genome fasta for the alignment.
    return get_dataset_with_type(
            alignment_group.reference_genome,
            Dataset.TYPE.REFERENCE_GENOME_FASTA).get_absolute_location()

def _create_output_dir(alignment_group):
    # Prepare a directory to put the output files.
    # We'll put them in
    #     /projects/<project_uid>/alignment_groups/vcf/<variant tool>/
    #     <alignment_type>.vcf
    # We'll save these for now, maybe it's not necessary later.
    vcf_dir = os.path.join(alignment_group.get_model_data_dir(), 'vcf')
    ensure_exists_0775_dir(vcf_dir)
    return vcf_dir

def _find_valid_sample_alignments(alignment_group, alignment_type):
    """ Returns a list sample alignment objects for an alignment,
        skipping those that failed. """
    sample_alignment_list = (
            alignment_group.experimentsampletoalignment_set.all())

    # Filter out mis-aligned files.
    # TODO: Should we show in the UI that some alignments failed and are
    # being skipped?
    def _is_successful_alignment(sample_alignment):
        bam_dataset = get_dataset_with_type(sample_alignment, alignment_type)
        return bam_dataset.status == Dataset.STATUS.READY
    sample_alignment_list = [sample_alignment for sample_alignment in
            sample_alignment_list if _is_successful_alignment(sample_alignment)]

    if len(sample_alignment_list) == 0:
        raise Exception('No successful alignments, Freebayes cannot proceed.')

    bam_files = _get_dataset_paths(sample_alignment_list, alignment_type)

    # Keep only valid bam_files
    valid_bam_files = []
    for bam_file in bam_files:
        if bam_file is None:
            continue
        if not os.stat(bam_file).st_size > 0:
            continue
        valid_bam_files.append(bam_file)
    assert len(valid_bam_files) == len(sample_alignment_list), (
            "Expected %d bam files, but found %d" % (
                    len(sample_alignment_list), len(bam_files)))
    return sample_alignment_list


@task
@project_files_needed
def find_variants_with_tool(alignment_group, variant_params):
    """Applies a variant caller to the alignment data contained within
    alignment_group.

    Args:
        alignment_group: AlignmentGroup with all alignments complete.
        variant_params: Triple (tool_name, vcf_dataset_type, tool_function).

    Returns:
        Boolean indicating whether we made it through this entire function.
    """
    common_params = get_common_tool_params(alignment_group)
    tool_name, vcf_dataset_type, tool_function = variant_params

    # Finding variants means that all the aligning is complete, so now we
    # are VARIANT_CALLING.
    alignment_group.status = AlignmentGroup.STATUS.VARIANT_CALLING
    alignment_group.save()

    # Create subdirectory for this tool
    tool_dir = os.path.join(common_params['output_dir'], tool_name)
    ensure_exists_0775_dir(tool_dir)
    vcf_output_filename = os.path.join(tool_dir,
            uppercase_underscore(common_params['alignment_type']) + '.vcf')

    # Run the tool
    tool_succeeded = tool_function(
            vcf_output_dir= tool_dir,
            vcf_output_filename= vcf_output_filename,
            **common_params)
    if not tool_succeeded:
        return False

    # Add dataset
    # If a Dataset already exists, delete it, might have been a bad run.
    existing_set = Dataset.objects.filter(
            type=vcf_dataset_type,
            label=vcf_dataset_type,
            filesystem_location=clean_filesystem_location(vcf_output_filename),
    )
    if len(existing_set) > 0:
        existing_set[0].delete()

    dataset = Dataset.objects.create(
            type=vcf_dataset_type,
            label=vcf_dataset_type,
            filesystem_location=clean_filesystem_location(vcf_output_filename),
    )
    alignment_group.dataset_set.add(dataset)

    # Do the following only for freebayes; right now just special if condition
    if tool_name == 'freebayes':
        # For now, automatically run snpeff if a genbank annotation is
        # available.
        # If no annotation, then skip it, and pass the unannotated vcf type.
        if alignment_group.reference_genome.is_annotated():
            run_snpeff(alignment_group, Dataset.TYPE.BWA_ALIGN)
            vcf_dataset_type = VCF_ANNOTATED_DATASET_TYPE
        else:
            vcf_dataset_type = VCF_DATASET_TYPE

    # Tabix index and add the VCF track to Jbrowse
    add_vcf_track(alignment_group.reference_genome, alignment_group,
        vcf_dataset_type)

    # Parse the resulting vcf, grab variant objects
    parse_alignment_group_vcf(alignment_group, vcf_dataset_type)

    flag_variants_from_bed(alignment_group, Dataset.TYPE.BED_CALLABLE_LOCI)

    return True


def flag_variants_from_bed(alignment_group, bed_dataset_type):

    sample_alignments = alignment_group.experimentsampletoalignment_set.all()
    for sample_alignment in sample_alignments:

        # If there is no callable_loci bed, skip the sample alignment.
        # TODO: Make this extensible to other BED files we might have
        callable_loci_bed = get_dataset_with_type(
                entity=sample_alignment,
                type=Dataset.TYPE.BED_CALLABLE_LOCI)

        if not callable_loci_bed: continue

        # need to add sample_alignment and bed_dataset here.
        add_variants_to_set_from_bed(
                sample_alignment= sample_alignment,
                bed_dataset= callable_loci_bed)


def run_freebayes(fasta_ref, sample_alignments, vcf_output_dir,
        vcf_output_filename, alignment_type, **kwargs):
    """Run freebayes using the bam alignment files keyed by the alignment_type
    for all Genomes of the passed in ReferenceGenome.

    NOTE: If a Genome doesn't have a bam alignment file with this
    alignment_type, then it won't be used.

    Returns:
        Boolean, True if successfully made it to the end, else False.
    """
    vcf_dataset_type = VCF_DATASET_TYPE

    bam_files = _get_dataset_paths(sample_alignments, alignment_type)

    # Build up the bam part of the freebayes binary call.
    bam_part = []
    for bam_file in bam_files:
        bam_part.append('--bam')
        bam_part.append(bam_file)

    # Build the full command and execute it for all bam files at once.
    full_command = (['%s/freebayes/freebayes' %  TOOLS_DIR] + bam_part + [
        '--fasta-reference', fasta_ref,
        '--pvar', '0.001',
        '--ploidy', '2',
        '--min-alternate-fraction', '.3',
        '--hwe-priors-off',
        '--binomial-obs-priors-off',
        '--use-mapping-quality',
        '--min-base-quality', '25',
        '--min-mapping-quality', '30'
    ])

    with open(vcf_output_filename, 'w') as fh:
        subprocess.check_call(full_command, stdout=fh)

    return True # success


def run_pindel(fasta_ref, sample_alignments, vcf_output_dir,
        vcf_output_filename, alignment_type, **kwargs):
    """Run pindel to find SVs."""
    vcf_dataset_type = VCF_PINDEL_TYPE

    if not os.path.isdir('%s/pindel' % TOOLS_DIR):
        raise Exception('Pindel is not installed. Aborting.')

    bam_files = _get_dataset_paths(sample_alignments, alignment_type)
    samples = [sa.experiment_sample for sa in sample_alignments]
    insert_sizes = [get_insert_size(sa) for sa in sample_alignments]

    assert len(bam_files) == len(insert_sizes)

    # Create pindel config file
    pindel_config = os.path.join(vcf_output_dir, 'pindel_config.txt')
    at_least_one_config_line_written = False
    with open(pindel_config, 'w') as fh:
        for bam_file, sample, insert_size in zip(
                bam_files, samples, insert_sizes):

            # Skip bad alignments.
            if insert_size == -1:
                continue
            fh.write('%s %s %s\n' % (bam_file, insert_size, sample.uid))
            at_least_one_config_line_written = True

    if not at_least_one_config_line_written:
        raise Exception
        return False # failure

    # Build the full pindel command.
    pindel_root = vcf_output_filename[:-4]  # get rid of .vcf extension
    subprocess.check_call(['%s/pindel/pindel' % TOOLS_DIR,
        '-f', fasta_ref,
        '-i', pindel_config,
        '-c', 'ALL',
        '-o', pindel_root
    ])

    # convert all different structural variant types to vcf
    subprocess.check_call(['%s/pindel/pindel2vcf' % TOOLS_DIR,
        '-P', pindel_root,
        '-r', fasta_ref,
        '-R', 'name',
        '-d', 'date',
        '-mc', '1',  # just need one read to show 1/1 in vcf
    ])

    postprocess_pindel_vcf(vcf_output_filename)

    return True # success

def run_lumpy(fasta_ref, sample_alignments, vcf_output_dir,
    vcf_output_filename, alignment_type, **kwargs):
    """
    https://github.com/arq5x/lumpy-sv
    """

    # TODO, look for these three in kwargs before setting?

    global_lumpy_options = ['-mw',1,'-tt',0.0]

    # split reads should be given a higher weight than discordant pairs.
    shared_sr_options= {
        'back_distance':20,
        'weight':2,
        'min_mapping_threshold':20
    }

    shared_pe_options = {
        'back_distance' : 20,
        'weight' : 1,
        'min_mapping_threshold' : 20,
        'discordant_z' : 4
    }

    pe_sample_options = []
    sr_sample_options = []

    # lumpy uses integer sample IDs, so this is a lookup table.
    sample_id_dict = {}
    sample_uid_order = []

    # build up the option strings for each sample
    for i, sa in enumerate(sample_alignments):

        sample_uid = sa.experiment_sample.uid
        sample_uid_order.append(sample_uid)
        sample_id_dict[i] = sample_uid

        bam_pe_dataset = get_discordant_read_pairs(sa)
        bam_pe_file = bam_pe_dataset.get_absolute_location()

        bam_sr_dataset = get_split_reads(sa)
        bam_sr_file = bam_sr_dataset.get_absolute_location()

        ins_size, ins_stdev = get_insert_size(sa, stdev=True)
        histo_file = os.path.join(sa.get_model_data_dir(),
                'insert_size_histogram.txt')
        read_length = get_read_length(sa)

        if bam_pe_dataset.status != Dataset.STATUS.FAILED and bam_pe_file:

            assert os.path.isfile(bam_pe_file), (
                    '{file} is not empty but is not a file!'.format(
                            file=bam_sr_file))

            pe_sample_str = ','.join([
                'bam_file:'+bam_pe_file,
                'histo_file:'+histo_file,
                'mean:'+str(ins_size),
                'stdev:'+str(ins_stdev),
                'read_length:'+str(read_length),
                'min_non_overlap:'+str(read_length),
                'id:'+str(i),
                ] + ['%s:%d' % (k,v) for k,v in shared_pe_options.items()])
        else:
            bam_pe_str = ''

        if bam_sr_dataset.status != Dataset.STATUS.FAILED and bam_sr_file:

            assert os.path.isfile(bam_sr_file), (
                    '{file} is not empty but is not a file!'.format(
                            file=bam_sr_file))

            sr_sample_str = ','.join([
                'bam_file:'+bam_sr_file,
                'id:'+str(i),
                ] + ['%s:%d' % (k,v) for k,v in shared_sr_options.items()])
        else:
            sr_sample_str = ''

        if  bam_pe_dataset.status != Dataset.STATUS.FAILED and bam_pe_file:
            pe_sample_options.extend(['-pe',pe_sample_str])
        if bam_sr_dataset.status != Dataset.STATUS.FAILED and bam_sr_file:
            sr_sample_options.extend(['-sr',sr_sample_str])

    # combine the options and call lumpy
    combined_options = [str(o) for o in (global_lumpy_options +
        pe_sample_options + sr_sample_options)]

    lumpy_output = (os.path.splitext(vcf_output_filename)[0] + '_' +
            sample_uid + '_' + '.txt')

    print combined_options

    try:
        with open(lumpy_output, 'w') as fh:
            subprocess.check_call(
                    ['%s/lumpy/lumpy' % TOOLS_DIR] + combined_options,
                    stdout=fh)
    except subprocess.CalledProcessError, e:
        print 'Lumpy failed. Command: {command}'.format(
                command=['%s/lumpy/lumpy' % TOOLS_DIR] + combined_options)
        raise e

    # convert lumpy output to VCF.
    # 1. chromosome 1
    # 2. interval 1 start
    # 3. interval 1 end
    # 4. chromosome 2
    # 5. interval 2 start
    # 6. interval 2 end
    # 7. id
    # 8. evidence set score
    # 9. strand 1
    # 10. strand 2
    # 11. type
    # 12. id of samples containing evidence for this breakpoint
    # 13. strand configurations observed in the evidence set
    # 14. point within the two breakpoint with the maximum probability
    # 15. segment of each breakpoint that contains 95% of the probability
    fieldnames = [
        'chr_1',
        'ivl_1_start',
        'ivl_1_end',
        'chr_2',
        'ivl_2_start',
        'ivl_2_end',
        'id',
        'evidence_score',
        'strand_1',
        'strand_2',
        'svtype',
        'sample_ids',
        'strand_configs',
        'breakpoint_max',
        'breakpoint_95_reg'
    ]

    lumpy_vcf_header = "\n".join([
        '##fileformat=VCFv4.0',
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End position of the variant described in this record">',
        '##INFO=<ID=END_CHR,Number=-1,Type=String,Description="End chromosome of the variant described in this record">',
        '##INFO=<ID=IMPRECISE,Number=0,Type=Flag,Description="Imprecise structural variation">',
        '##INFO=<ID=SVLEN,Number=-1,Type=Integer,Description="Difference in length between REF and ALT alleles">',
        '##INFO=<ID=SVTYPE,Number=-1,Type=String,Description="Type of structural variant">',
        '##INFO=<ID=STRAND_1,Number=1,Type=String,Description="Strand Orientation of SV Start">',
        '##INFO=<ID=STRAND_2,Number=1,Type=String,Description="Strand Orientation of SV End">',
        '##INFO=<ID=METHOD,Number=1,Type=String,Description="SV Caller used to predict">',
        '##INFO=<ID=DP,Number=1,Type=String,Description="combined depth across samples">',
        '##ALT=<ID=DEL,Description="Deletion">',
        '##ALT=<ID=DUP,Description="Duplication">',
        '##ALT=<ID=INS,Description="Insertion of novel sequence">',
        '##ALT=<ID=INV,Description="Inversion">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=AO,Number=1,Type=Integer,Description="Alternate Allele Observations">'])

    lumpy_vcf_col_header = "\t".join([
        '#CHROM','POS','ID','REF','ALT','QUAL','FILTER','INFO','FORMAT']+sample_uid_order)

    def lumpy_output_line_to_vcf(fields, sample_id_dict, sample_uid_order):

        bp_start, bp_end = [i.split(':') for i in
                fields['breakpoint_max'][4:].split(';')]

        _, fields['ivl_1'] = bp_start
        _, fields['ivl_2'] = bp_end

        vcf_format_str = '\t'.join(['{chr_1}','{ivl_1}',
            '.','N','<{svtype}>','{evidence_score}',
            '.','{info_string}','GT:AO','{genotypes}'])

        info_format_str = (
                'IMPRECISE;'
                'SVTYPE={svtype};'
                'END={ivl_2};'
                'END_CHR={chr_2};'
                'STRAND_1={strand_1};'
                'STRAND_2={strand_2};'
                'SVLEN={svlen};'
                'METHOD=LUMPY;'
                'DP={reads}')

        # SVLEN is only calculable (easily) if it is an intrachromosal event
        if fields['chr_1'] == fields['chr_2']:
            fields['svlen'] = int(fields['ivl_2']) - int(fields['ivl_1'])

            # negative number if it's a deletion
            if 'DEL' in fields['svtype']:
                fields['svlen'] = -1 * fields['svlen']
        else:
            fields['svlen'] == 0

        # looks like TYPE:DELETION
        # first 3 letters, should be INV, DUP, DEL, INS, etc
        fields['svtype'] = fields['svtype'][5:8]

        # Split up the sample ids
        gt_dict = dict([(sample_uid, '.')
                for sample_uid in sample_id_dict.values()])
        fields['sample_ids'] = fields['sample_ids'][4:].split(';')

        # initialize total read count for this SV
        fields['reads'] = 0

        # make the genotype field strings per sample
        for id_field in fields['sample_ids']:
            sample_id, reads = id_field.split(',')
            fields['reads'] += int(reads)
            # convert lumpy sample ID to our UID
            sample_uid = sample_id_dict[int(sample_id)]
            gt_dict[sample_uid] = ':'.join(['1/1',reads])

        fields['genotypes'] = '\t'.join(
                [gt_dict[uid] for uid in sample_uid_order])

        fields['info_string'] = info_format_str.format(
                **fields)

        return vcf_format_str.format(**fields)

    with open(lumpy_output, 'r') as lumpy_in:
        with open(vcf_output_filename, 'w') as lumpy_out:
            print >> lumpy_out, lumpy_vcf_header
            print >> lumpy_out, lumpy_vcf_col_header
            for line in lumpy_in:
                fields = dict(zip(fieldnames, line.split()))
                print >> lumpy_out, lumpy_output_line_to_vcf(fields,
                        sample_id_dict, sample_uid_order)

    return True



def run_delly(fasta_ref, sample_alignments, vcf_output_dir,
        vcf_output_filename, alignment_type, **kwargs):
    """Run delly to find SVs."""
    vcf_dataset_type = VCF_DELLY_TYPE

    if not os.path.isdir('%s/delly' % TOOLS_DIR):
        raise Exception('Delly is not installed. Aborting.')

    delly_root = vcf_output_filename[:-4]  # get rid of .vcf extension
    transformations = ['DEL', 'DUP', 'INV']
    vcf_outputs = map(lambda transformation:
            '%s_%s.vcf' % (delly_root, transformation), transformations)

    # Rename bam files, because Delly uses the name of the file as sample uid.
    # Use cp instead of mv, because other sv callers will be reading from the
    #   original bam file.

    bam_files = _get_dataset_paths(sample_alignments, alignment_type)
    samples = [sa.experiment_sample for sa in sample_alignments]

    new_bam_files = []
    for bam_file, sample in zip(bam_files, samples):
        new_bam_file = os.path.join(
                os.path.dirname(bam_file), sample.uid + '.bam')
        subprocess.check_call(['cp', bam_file, new_bam_file])
        subprocess.check_call(['cp', bam_file + '.bai', new_bam_file + '.bai'])
        new_bam_files.append(new_bam_file)

    # run delly for each type of transformation
    for transformation, vcf_output in zip(transformations, vcf_outputs):

        print ['%s/delly/delly' % TOOLS_DIR,
            '-t', transformation,
            '-o', vcf_output,
            '-g', fasta_ref] + new_bam_files

        # not checked_call, because delly errors if it doesn't find any SVs
        subprocess.call(['%s/delly/delly' % TOOLS_DIR,
            '-t', transformation,
            '-o', vcf_output,
            '-g', fasta_ref] + new_bam_files)



    # combine the separate vcfs for each transformation
    vcf_outputs = filter(lambda file: os.path.exists(file), vcf_outputs)
    if vcf_outputs:
        temp_vcf = os.path.join(vcf_output_dir, 'temp_vcf')
        with open(temp_vcf, 'w') as fh:
            subprocess.check_call(['vcf-concat'] + vcf_outputs, stdout=fh)
        with open(vcf_output_filename, 'w') as fh:
            subprocess.check_call(['vcf-sort', temp_vcf], stdout=fh)
        subprocess.check_call(['rm', temp_vcf])
    else:
        # hack: create empty vcf
        subprocess.check_call(['touch', delly_root])
        subprocess.check_call(['%s/pindel/pindel2vcf' % TOOLS_DIR,
            '-p', delly_root,  # TODO does this work?
            '-r', fasta_ref,
            '-R', 'name',
            '-d', 'date'
        ])

    # Delete temporary renamed bam files
    for bam_file in new_bam_files:
        subprocess.check_call(['rm', bam_file])
        subprocess.check_call(['rm', bam_file + '.bai'])

    postprocess_delly_vcf(vcf_output_filename)

    return True # success

# Get paths for each of the dataset files.
def _get_dataset_paths(sample_alignment_list, dataset_type):

    dataset_locations = []

    # These sample alignments should have already
    # been validated in _find_valid_sample_alignments...
    for sample_alignment in sample_alignment_list:
        dataset = get_dataset_with_type(sample_alignment, dataset_type)
        dataset_locations.append(dataset.get_absolute_location())

    return dataset_locations


def _common_postprocess_vcf(vcf_reader):
    # Do postprocessing in common to Pindel and Delly VCFs.
    modified_header_lines = []

    # These properties should be part of VA_DATA, although SV tools will
    #   output at most one property in each row and set Number=1 in the
    #   VCF header line. The easiest way to store these properties in
    #   VA_DATA is just to postprocess the VCF here and change these
    #   header lines to all say Number=A.
    va_properties = ['SVTYPE', 'SVLEN']
    def modify_header(header_line):
        if any([prop in header_line for prop in va_properties]):
            header_line = header_line.replace('Number=1', 'Number=A')
            modified_header_lines.append(header_line)
        return header_line
    vcf_reader._header_lines = map(modify_header, vcf_reader._header_lines)

    # Also add a field for METHOD.
    method_header_line = '##INFO=<ID=METHOD,Number=1,Type=String,' + \
        'Description="Type of approach used to detect SV">\n'
    modified_header_lines.append(method_header_line)
    vcf_reader._header_lines.append(method_header_line)

    # Now update the header lines in vcf_reader.infos map as well.
    parser = vcf.parser._vcf_metadata_parser()
    for header_line in modified_header_lines:
        key, val = parser.read_info(header_line)
        vcf_reader.infos[key] = val


# Postprocess vcfs output by Pindel and Delly, so their information is
#   customized to whatever is needed in Millstone, and the format is
#   the same as that of Freebayes.
def postprocess_pindel_vcf(vcf_file):
    vcf_reader = vcf.Reader(open(vcf_file))

    _common_postprocess_vcf(vcf_reader)

    # Write the modified VCF to a temp file.
    vcf_writer = vcf.Writer(open(vcf_file + '.tmp', 'a'), vcf_reader)
    for record in vcf_reader:
        if 'SVLEN' not in record.__dict__['INFO']:
            continue  # should not happen

        # pindel uses negative SVLEN for deletions; make them positive
        # always have one entry
        svlen = abs(record.__dict__['INFO']['SVLEN'][0])
        record.__dict__['INFO']['SVLEN'] = [svlen]

        if svlen < 10:  # ignore small variants
            continue

        # update METHOD field
        record.__dict__['INFO']['METHOD'] = 'PINDEL'

        vcf_writer.write_record(record)

    # move temporary file back
    subprocess.check_call(['mv', vcf_file + '.tmp', vcf_file])

def postprocess_delly_vcf(vcf_file):
    vcf_reader = vcf.Reader(open(vcf_file))

    _common_postprocess_vcf(vcf_reader)

    vcf_writer = vcf.Writer(open(vcf_file + '.tmp', 'a'), vcf_reader)
    for record in vcf_reader:
        record.__dict__['INFO']['METHOD'] = 'DELLY'
        vcf_writer.write_record(record)

    subprocess.check_call(['mv', vcf_file + '.tmp', vcf_file])
