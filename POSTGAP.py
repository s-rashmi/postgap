#! /usr/bin/env python

"""

Copyright [1999-2018] EMBL-European Bioinformatics Institute

Licensed under the Apache License, Version 2.0 (the "License")
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

		 http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

"""

"""

	Please email comments or questions to the public Ensembl
	developers list at <http://lists.ensembl.org/mailman/listinfo/dev>.

	Questions may also be sent to the Ensembl help desk at
	<http://www.ensembl.org/Help/Contact>.

"""
import sys
import argparse
from argparse import RawTextHelpFormatter
import collections
import json
import re
import logging
import logging.config
import cPickle as pickle

import postgap
import postgap.GWAS
import postgap.Cisreg
import postgap.Reg
import postgap.EFO
import postgap.Globals
import postgap.Integration
from postgap.Utils import *


import sys
from pprint import pformat

r2_cache = collections.defaultdict(dict)
GRCh38_snp_locations= dict()
known_chroms = map(str, range(1,22)) + ['X','Y']

'''
Development TODO list:

A. Datasets to integrate:
	-Cisregulatory annotations:
		PCHIC (STOPGAP Scoring: Single cell line: +1, multiple cell lines: +2)

	-Epigenetic activity:
		PhyloP (STOPGAP Scoring: FPR 0-0.6: +2, 0.6-0.85: +1,0.85-1: +0)

B. Code improvements:
	Pathways analysis (Downstream)
	Take into account population composition in LD calcs

C. Model improvements:
	Replace PICS with Bayesian model
	Fine mapping of summary data
	Tissue selection

'''

def main():
	"""

		Reads commandline parameters, prints corresponding associated genes with evidence info

	"""
	
	logging.config.fileConfig('configuration/logging.conf')

	options = get_options()
	
	logging.info("Starting postgap with the following options:")
	logging.info(pformat(options))
	efo_iris = []

	if options.efos is not None:
		efo_iris = postgap.EFO.query_iris_for_efo_short_form_list(options.efos)
	else:
		efo_iris = filter(lambda X: X is not None, (postgap.EFO.suggest(disease) for disease in options.diseases))

	if options.child_terms:
		# Expand list of EFOs to children, concatenate, remove duplicates
		expanded_efo_iris = efo_iris + concatenate(map(postgap.EFO.children, efo_iris))
	else:
		expanded_efo_iris = efo_iris

	if options.GWAS is not None:
		postgap.Globals.GWAS_adaptors = options.GWAS

	if options.Cisreg is not None:
		postgap.Globals.Cisreg_adaptors = options.Cisreg

	if options.Reg is not None:
		postgap.Globals.Reg_adaptors = options.Reg

	if len(options.diseases) > 0 or len(expanded_efo_iris) > 0:
		logging.info("Starting diseases_to_genes")
		res = postgap.Integration.diseases_to_genes(options.diseases, expanded_efo_iris, "CEPH", options.tissues)
		pickle.dump(res, open("postgap_output", "w")) # DEBUG remove hard coded path
		logging.info("Done with diseases_to_genes")
	elif options.rsID is not None:
		res = postgap.Integration.rsIDs_to_genes(options.rsID, options.tissues)
	elif options.coords is not None:
		snp = postgap.DataModel.SNP(rsID = options.coords[0], chrom = options.coords[1], pos = int(options.coords[2]))
		if options.tissues is None:
			options.tissues = ["Whole_Blood"]
		res = postgap.Integration.ld_snps_to_genes([snp], options.tissues)

	if options.output is None:
		output = sys.stdout
	else:
		output = open(options.output, "w")

	if options.json_output:
		formatted_results = json.dumps(objectToDict(res))
	elif options.rsID is None and options.coords is None:
		formatted_results = pretty_output(res)
	else:
		formatted_results = pretty_snp_output(res)

	output.write(formatted_results + "\n")

def get_options():
    """

        Reads commandline parameters
        Returntype:
            {
                diseases: [ string ],
                populations: [ string ],
                tissues: [ string ],
            }

    """
    parser = argparse.ArgumentParser(description=
    """
    Search GWAS/Regulatory/Cis-regulatory databases for causal genes. 
    
    Tab delimited format:
    1	ld_snp_rsID	rsID of putative causal SNP (LD SNP) being tested
    2	chrom		chromosome name of LD SNP
    3	pos		position of LD SNP on chromosome
    4	gene_symbol	HGNC symbol of gene being tested
    5	gene_id		Ensembl ID of gene being tested
    6	gene_chrom	Chromosome name of gene being tested
    7	gene_tss	Position on the chromosome of TSS of gene being tested
    8	disease_name	Normalised name of disease or trait being tested
    9	disease_efo_id	Ontology ID of disease or trait being tested
    10	score		Stopgap V2G score tying LD SNP to gene	
    11	rank		Rank of gene's V2G score among other genes tied to LD SNP
    12	r2		Pearson correlation of LD SNP to the GWAS SNP with lowest p-value
    13	gwas_source	Source database of GWAS SNPs
    14	gwas_snp	SNPs associated to disease by GWAS
    15	gwas_pvalue	P-values of reported associations
    16	gwas_size	Sample sizes of reported associations
    17	gwas_pmid	Source publication PubmedID of reported associations
    18	gwas_reported_trait	Disease or trait of reported association
    19	ld_snp_is_gwas_snp	1 if LD SNP is one of the GWAS SNPs, 0 otherwise
    20	vep_terms	VEP consequence terms associated to LD_SNP
    21	vep_sum		Sum of consequence impacts of LD SNP across all transcripts of gene:
				'HIGH': 4,
				'MEDIUM': 3,
				'LOW': 2,
				'MODIFIER': 1,
				'MODERATE': 1
    22	vep_mean	Mean of consequence impacts of LD SNP across all transcripts of gene:
				'HIGH': 4,
				'MEDIUM': 3,
				'LOW': 2,
				'MODIFIER': 1,
				'MODERATE': 1
    23	GTEx		1 if LD SNP is an eQTL to the gene, with p-value < 2.5e-5 
    24	VEP		Max of consequence impacts of LD SNP across all transcripts of gene:
				'HIGH': 4,
				'MEDIUM': 3,
				'LOW': 2,
				'MODIFIER': 1,
				'MODERATE': 1
    25	Fantom5		Score of FANTOM5 link between LD SNP and gene, normalised for FDR
    26	DHS		Score of ENCODE DHS link between LD SNP and gene, normalised for FDR
    27	PCHiC		Sum of scores of CHiCAGO links between LD SNP and gene across tissues, normalised for FDR
    28	Nearest		1 if gene has the nearest protein-coding TSS to LD SNP, 0 otherwise	
    29	Regulome	2 if LD SNP is of Category 1 or 2, 1 if LD SNP in Category 3
    """,
    formatter_class = RawTextHelpFormatter
		    )

    GWAS_options = ["GWAS_Catalog", "GRASP", "Phewas_Catalog", "GWAS_DB"]
    CisReg_options = ["GTEx", "VEP", "Fantom5", "DHS", "PCHiC", "Nearest"]
    Reg_options = ["Regulome", "VEP_reg"]

    parser.add_argument('--efos', nargs='*')
    parser.add_argument('--diseases', nargs='*')
    parser.add_argument('--rsID')
    parser.add_argument('--coords', nargs=3)
    # parser.add_argument('--populations', nargs='*', default=['1000GENOMES:phase_3:GBR'])
    parser.add_argument('--tissues', nargs='*')
    parser.add_argument('--output')
    parser.add_argument('--species', nargs='*', default = 'Human')
    parser.add_argument('--database_dir', dest = 'databases', default = 'databases')
    parser.add_argument('--debug', '-g', action = 'store_true')
    parser.add_argument('--json_output', '-j', action = 'store_true')
    parser.add_argument('--child_terms', action = 'store_true')
    parser.add_argument('--GWAS', default=None, nargs='*', choices=(GWAS_options))
    parser.add_argument('--Cisreg', default=None, nargs='*', choices=(CisReg_options))
    parser.add_argument('--Reg', default=None, nargs='*', choices=(Reg_options))
    parser.add_argument('--bayesian', action = 'store_true')
    parser.add_argument('--work_dir', default = 'postgap_temp_work_dir')
    parser.add_argument('--summary_stats')
    options = parser.parse_args()

    postgap.Globals.DATABASES_DIR = options.databases
    postgap.Globals.SPECIES = options.species
    postgap.Globals.DEBUG = postgap.Globals.DEBUG or options.debug
    postgap.Globals.GWAS_SUMMARY_STATS_FILE = options.summary_stats
    postgap.Globals.PERFORM_BAYESIAN = options.bayesian
    
    if options.efos is not None:
        postgap.Globals.work_directory = options.work_dir + "/" + "_".join(options.efos)
    
    if options.diseases is not None:
        postgap.Globals.work_directory = options.work_dir + "/" + "_".join(options.diseases)
    
    postgap.Globals.finemap_gwas_clusters_directory = postgap.Globals.work_directory + "/gwas_clusters"
    postgap.Globals.finemap_eqtl_clusters_directory = postgap.Globals.work_directory + "/eqtl_clusters"

    assert postgap.Globals.DATABASES_DIR is not None
    assert options.rsID is None or (options.efos is None and options.diseases is None)
    assert options.rsID is not None or options.efos is not None or options.diseases is not None or options.coords is not None
    
    import os
    assert os.path.isdir(postgap.Globals.DATABASES_DIR), "--database_dir parameter " + options.databases + " does not point to an existing directory!"
    assert os.path.exists(postgap.Globals.DATABASES_DIR + "/GRASP.txt"), "Can't find GRASP.txt in " + options.databases


    if options.diseases is None:
        options.diseases = []

    return options

def pretty_snp_output(associations):
	"""

		Prints association stats in roughly the same format as STOPGAP for a cluster of SNPs
		Args: [ GeneSNP_Association ]
		Returntype: String

	"""
	header = "\t".join(['snp_rsID', 'chrom', 'pos', 'gene_symbol', 'gene_id', 'score'] + [obj.display_name for obj in postgap.Cisreg.sources + postgap.Reg.sources])
	content = map(pretty_snp_association, associations)
	return "\n".join([header] + content) + "\n"

def pretty_snp_association(association):
	"""

		Prints association stats in roughly the same format as STOPGAP for a cluster of SNPs
		Args: GeneSNP_Association
		Returntype: String

	"""
	snp = association.snp
	gene_name = association.gene.name
	gene_id = association.gene.id
	score = association.score

	results = [snp.rsID, snp.chrom, str(snp.pos), gene_name, gene_id, str(score)]
	results += [str(association.intermediary_scores[functional_source.display_name]) for functional_source in postgap.Cisreg.sources]
	return "\t".join(results)

def pretty_output(associations):
	"""

		Prints association stats in roughly the same format as STOPGAP for a cluster of SNPs
		Args: [ GeneCluster_Association ]
		Returntype: String

	"""
	header = "\t".join(['ld_snp_rsID', 'chrom', 'pos', 'GRCh38_chrom', 'GRCh38_pos', 'afr_maf', 'amr_maf', 'eas_maf', 'eur_maf', 'sas_maf', 'gene_symbol', 'gene_id', 'gene_chrom', 'gene_tss', 'GRCh38_gene_chrom', 'GRCh38_gene_pos', 'disease_name', 'disease_efo_id', 'score', 'rank', 'r2', 'cluster_id', 'gwas_source', 'gwas_snp', 'gwas_pvalue', 'gwas_pvalue_description', 'gwas_odds_ratio', 'gwas_beta', 'gwas_size', 'gwas_pmid', 'gwas_study', 'gwas_reported_trait', 'ls_snp_is_gwas_snp', 'vep_terms', 'vep_sum', 'vep_mean'] + [source.display_name for source in postgap.Cisreg.sources + postgap.Reg.sources])
	content = filter(lambda X: len(X) > 0, map(pretty_cluster_association, associations))
	return "\n".join([header] + content)

def pretty_cluster_association(association):
	"""

		Prints association stats in roughly the same format as STOPGAP for a cluster of SNPs
		Args: GeneCluster_Association
		Returntype: String

	"""
	results = genecluster_association_table(association)
	return "\n".join("\t".join([unicode(element).encode('utf-8') for element in row]) for row in results)

def genecluster_association_table(association):
	"""

		Returns association stats in roughly the same format as STOPGAP for a cluster of SNPs
		Arg1: GeneCluster_Association
		Returntype: [[ string or float ]]

	"""
	results = []
	for snp1 in association.cluster.ld_snps:
		for snp2 in association.cluster.ld_snps:
			if snp1.rsID not in r2_cache or snp2.rsID not in r2_cache[snp1.rsID]:
				ld_snp_ids, r_matrix = postgap.LD.get_pairwise_ld(association.cluster.ld_snps)
				r_index = dict((snp, index) for index, snp in enumerate(ld_snp_ids))
				for SNPA in ld_snp_ids:
					for SNPB in ld_snp_ids:
						r2_cache[SNPA][SNPB] = r_matrix.item((r_index[SNPA], r_index[SNPB]))**2
				break

	GRCh38_gene = postgap.Ensembl_lookup.get_ensembl_gene(association.gene.id, postgap.Ensembl_lookup.GRCH38_ENSEMBL_REST_SERVER)
	if GRCh38_gene is None:
		return []

	if GRCh38_gene.chrom not in known_chroms:
		return []

	GRCh38_snps = postgap.Ensembl_lookup.get_snp_locations([ld_snp.rsID for ld_snp in association.cluster.ld_snps if ld_snp.rsID not in GRCh38_snp_locations], postgap.Ensembl_lookup.GRCH38_ENSEMBL_REST_SERVER)

	for snp in GRCh38_snps:
		GRCh38_snp_locations[snp.rsID] = snp

	cluster_id = hash(json.dumps(association.cluster.gwas_snps))

	for gwas_snp in association.cluster.gwas_snps:
		for gwas_association in gwas_snp.evidence:
			pmid = clean_pmid(gwas_association.publication)
			if pmid is None:
				continue

			for gene_snp_association in association.evidence:
				if gene_snp_association.snp.rsID not in GRCh38_snp_locations:
					continue

				if gene_snp_association.snp.chrom not in known_chroms or GRCh38_snp_locations[gene_snp_association.snp.rsID].chrom not in known_chroms:
					continue

				afr_maf = 'N/A'
				amr_maf = 'N/A'
				eas_maf = 'N/A'
				eur_maf = 'N/A'
				sas_maf = 'N/A'

				vep_terms = []
				for evidence in gene_snp_association.cisregulatory_evidence:
					if evidence.source == "VEP":
						vep_terms += evidence.info['consequence_terms']
				if len(vep_terms) > 0:
					vep_terms = ",".join(list(set(vep_terms)))
				else:
					vep_terms = "N/A"

				for evidence in gene_snp_association.regulatory_evidence:
					if evidence.source == "VEP_reg":
						MAFs = evidence.info['MAFs']
						if MAFs is not None:
							if 'afr_maf' in MAFs:
								afr_maf = MAFs['afr_maf']
							if 'amr_maf' in MAFs:
								amr_maf = MAFs['amr_maf']
							if 'eas_maf' in MAFs:
								eas_maf = MAFs['eas_maf']
							if 'eur_maf' in MAFs:
								eur_maf = MAFs['eur_maf']
							if 'sas_maf' in MAFs:
								sas_maf = MAFs['sas_maf']
							break

				if 'VEP_mean' in gene_snp_association.intermediary_scores:
					vep_mean = gene_snp_association.intermediary_scores['VEP_mean']
				else:
					vep_mean = 0

				if 'VEP_sum' in gene_snp_association.intermediary_scores:
					vep_sum = gene_snp_association.intermediary_scores['VEP_sum']
				else:
					vep_sum = 0

				r2_distance = read_pairwise_ld(gene_snp_association.snp, gwas_snp.snp)

				if r2_distance < 0.7:
					continue

				row = [
					gene_snp_association.snp.rsID, 
					gene_snp_association.snp.chrom, 
					gene_snp_association.snp.pos, 
					GRCh38_snp_locations[gene_snp_association.snp.rsID].chrom,
					GRCh38_snp_locations[gene_snp_association.snp.rsID].pos,
					afr_maf,
					amr_maf,
					eas_maf,
					eur_maf,
					sas_maf,
					association.gene.name, 
					association.gene.id, 
					association.gene.chrom, 
					association.gene.tss, 
					GRCh38_gene.chrom,
					GRCh38_gene.tss,
					gwas_association.disease.name,
					re.sub(".*/", "", gwas_association.disease.efo),
					gene_snp_association.score, 
					gene_snp_association.rank,
					r2_distance,
					cluster_id,
					gwas_association.source,
					gwas_association.snp.rsID,
					gwas_association.pvalue,
					gwas_association.pvalue_description,
					gwas_association.odds_ratio,
					gwas_association.beta_coefficient,
					gwas_association.sample_size,
					pmid,
					gwas_association.study,
					gwas_association.reported_trait,
					int(gene_snp_association.snp.rsID == gwas_snp.snp.rsID),
					vep_terms,
					vep_sum,
					vep_mean
				]

				row += [gene_snp_association.intermediary_scores[functional_source.display_name] for functional_source in postgap.Cisreg.sources + postgap.Reg.sources]
				results.append(row)

	return results

def clean_pmid(string):
	"""
		Cleans minor exceptions to PMID formatting, e.g. PUBMEDID:######### or just ##########
		Arg1: string
		Returntype: string
	"""
	if re.match("PMID[0-9]+", string) is not None:
		return string
	elif re.match("[0-9]+", string) is not None:
		return "PMID" + string
	elif re.match("PUBMEDID:[0-9]+", string) is not None:
		return re.sub("PUBMEDID:", "PMID", string)
	else:
		return None

def read_pairwise_ld(snp1, snp2):
	"""
		Returns r2 between two SNPs using matrix precomputed by LD.get_pairwise_ld
		Arg1: SNP
		Arg2: SNP
		Returntype: float
	"""
	if snp1.rsID == snp2.rsID:
		return 1
	if snp1.rsID in r2_cache and snp2.rsID in r2_cache:
		return r2_cache[snp1.rsID][snp2.rsID]
	else:
		return 0

if __name__ == "__main__":
	main()
