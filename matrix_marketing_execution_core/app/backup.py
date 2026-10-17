"""Operator backup/restore CLI. Backups contain sensitive records; keep outside Git."""
import argparse
import os
from pathlib import Path
import sqlite3
import subprocess


def backup_sqlite(source,target):
    target=Path(target)
    if target.exists():raise ValueError('Backup target already exists')
    with sqlite3.connect(source) as src,sqlite3.connect(target) as dst:src.backup(dst)
    target.chmod(0o600)


def restore_sqlite(source,target):
    if Path(target).exists():raise ValueError('Restore requires a new database path')
    with sqlite3.connect(f'file:{Path(source).resolve()}?mode=ro',uri=True) as src:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
        with sqlite3.connect(target) as dst:src.backup(dst)
    Path(target).chmod(0o600)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','restore']);parser.add_argument('file')
    args=parser.parse_args();url=os.getenv('DATABASE_URL','')
    if url:
        import psycopg
        env={**os.environ,'PGDATABASE':url}
        if args.action=='backup':
            if Path(args.file).exists():raise ValueError('Backup target already exists')
            command=['pg_dump','--format=custom','--file',args.file]
        else:
            with psycopg.connect(url) as db:
                if db.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]:raise ValueError('Restore requires an empty target database')
            # Use environment connection parameters instead of a secret in process argv.
            command=['pg_restore','--single-transaction','--exit-on-error','--no-owner','--no-acl','--dbname','',args.file]
        result=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if result.returncode:raise RuntimeError('PostgreSQL backup/restore failed; inspect securely on the operator host')
        if args.action=='backup':Path(args.file).chmod(0o600)
    else:
        db=os.getenv('MATRIX_EXEC_DB','./marketing_execution.db')
        if args.action=='backup':backup_sqlite(db,args.file)
        else:restore_sqlite(args.file,db)
    print(args.action+' completed; verify restored campaign counts before cutover')

if __name__=='__main__':main()
