#!/usr/bin/env python

import glob
import os
import pandas as pd
import numpy as np
import sqlite3


def construct_runname(inpath, replaces=['_glance', '_sci', '_meta', '_ss', '_ddf']):
    """given a directory path, construct a runname
    """
    result = os.path.basename(os.path.normpath(inpath))
    for rstring in replaces:
        result = result.replace(rstring, '')
    return result


def fast_gather(dirname='.', dbfilename='resultsDb_sqlite.db'):
    """Let's gather up a bunch of resultDb's 
    """

    potential_dirs = glob.glob(dirname + '/*/')

    db_files = []
    run_names = []
    for dname in potential_dirs:
        fname = os.path.join(dname, dbfilename)
        if os.path.isfile(fname):
            db_files.append(fname)
            run_names.append(construct_runname(dname))

    # querry to grab all the summary stats
    sql_q = 'select metrics.metricname, metrics.metricInfoLabel, summarystats.summaryName, summarystats.summaryValue '
    sql_q += 'FROM summarystats INNER JOIN metrics ON metrics.metric_id=summarystats.metric_id'

    rows = []

    for row_name, fname in zip(run_names, db_files):
        con = sqlite3.connect(fname)
        temp_df = pd.read_sql(sql_q, con)
        con.close()
        
        spaces = np.char.array([' ']*np.size(temp_df['metricName'].values))
        s1 = np.char.array(temp_df['metricName'].values.tolist())
        s2 = np.char.array(temp_df['metricInfoLabel'].values.tolist())
        s3 = np.char.array(temp_df['summaryName'].values.tolist())
        col_names = s1 + spaces + s2 + spaces + s3

        # Make a DataFrame row
        row = pd.DataFrame(temp_df['summaryValue'].values.reshape([1, temp_df['summaryValue'].values.size]),
                           columns=col_names, index=[row_name])
        rows.append(row)

    # Create final large DataFrame to hold everything
    all_cols = np.unique(np.concatenate([r.columns.values for r in rows]))
    u_names = np.unique(run_names)
    result_df = pd.DataFrame(np.zeros([u_names.size, all_cols.size])+np.nan, columns=all_cols, index=u_names)

    # Put each row into the final DataFrame
    for row_name, row in zip(run_names, rows):
        result_df.loc[row_name][row.columns] = np.ravel(row.values)

    return result_df


if __name__ == '__main__':

    result = fast_gather()
    result.to_hdf('summary.h5', key="stats")


