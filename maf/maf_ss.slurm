#!/bin/bash

## Job Name

#SBATCH --job-name=maf_ss3.0

## Allocation Definition

## On mox and ikt, the account and partition options should be the same.
#SBATCH --account=astro
#SBATCH --partition=astro

## Resources

## Nodes

#SBATCH --nodes=4
#SBATCH --ntasks-per-node=28

## Walltime (hours:min:sec) Do not specify a walltime substantially more than your job needs.

#SBATCH --time=4-48:00:00

## Memory per node. It is important to specify the memory since the default memory is very small.

## For mox, --mem may be more than 100G depending on the memory of your nodes.

## For ikt, --mem may be 58G or more depending on the memory of your nodes.

## See above section on "Specifying memory" for choices for --mem.

#SBATCH --mem=500G

## Specify the working directory for this job

#SBATCH --chdir=/gscratch/scrubbed/yoachim/sims_featureScheduler_runs3.0/maf

##turn on e-mail notification

#SBATCH --mail-type=ALL

#SBATCH --mail-user=yoachim@uw.edu

## export all your environment variables to the batch job session

#SBATCH --export=all

## Set up the evironment
source ~/anaconda3/etc/profile.d/conda.sh
conda activate guro
export OPENBLAS_NUM_THREADS=1

cd /gscratch/scrubbed/yoachim/sims_featureScheduler_runs3.0/maf

# sym-link in all the relevant dbs
find ../ -type f \( -iname "*10yrs.db" ! -path "*technical*" \) | xargs -I'{}' ln -s '{}' .


rm maf_ss.sh
# run the solar system etc
generate_ss
cat ss_script.sh >> maf_ss.sh
generate_ss --pop vatiras_granvik_10k
cat ss_script.sh >> maf_ss.sh

## run all the baseline commands in parallel
module load parallel-20170722
scontrol show hostnames > list_of_nodes
cpu_count=28

preamble='cd /gscratch/scrubbed/yoachim/sims_featureScheduler_runs3.0/maf  ; source setup.sh ; '

cat maf_ss.sh | xargs -I'{}' echo $preamble '{}' > maf_ss_final.sh


cat maf_ss_final.sh | parallel --sshloginfile list_of_nodes -j $cpu_count
