#$ -S /bin/bash
#$ -V
#$ -cwd
#$ -o grid_output
#$ -e grid_output
#$ -l h_vmem=12G
#$ -l scratch=9G
#$ -l paper
ARGS=`pull_args.py $*`

echo ${ARGS}

for f in ${ARGS}; do
    echo working on ${f}...
    omni_apply.py --omnipath /data4/paper/2014EoR/Analysis/ProcessedData/epoch3/omni_v2_v2/ ${f}
done

