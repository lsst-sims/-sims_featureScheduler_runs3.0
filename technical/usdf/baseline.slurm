#!/bin/bash

#SBATCH --partition=roma
#
#SBATCH --job-name=sched_baseline
#SBATCH --output=output-%j.txt
#SBATCH --error=output-%j.txt
#
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=3
#SBATCH --mem-per-cpu=20g
#
#SBATCH --time=0-24:10:00
#
#SBATCH --chdir=/sdf/group/rubin/user/yoachim/git_repos/sims_featureScheduler_runs3.0/technical/usdf/
## export all your environment variables to the batch job session
#SBATCH --export=all


echo "started script"

## Set up the evironment
source ~/anaconda3/etc/profile.d/conda.sh
conda activate rubin
export OPENBLAS_NUM_THREADS=1

cd /sdf/group/rubin/user/yoachim/git_repos/sims_featureScheduler_runs3.0/technical/usdf/

python draft2_updated_uzy.py --survey_length 20

ls *10yrs.db | xargs -I'{}' echo "glance_dir --db '{}'" > maf.sh
ls *10yrs.db | xargs -I'{}' echo "scimaf_dir --db '{}'" >> maf.sh
ls *10yrs.db | xargs -I'{}' echo "ddf_dir --db '{}'" >> maf.sh

cat maf.sh | parallel -j 3

