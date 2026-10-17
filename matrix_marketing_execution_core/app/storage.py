"""Small DB-API bridge for the existing SQL: SQLite development and PostgreSQL hosting."""
import sqlite3

class Row(dict):
    def __getitem__(self,key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    @property
    def rowcount(self):return self.cursor.rowcount
    def fetchone(self):
        row=self.cursor.fetchone()
        return Row(row) if row is not None else None
    def fetchall(self):return [Row(r) for r in self.cursor.fetchall()]
    def __iter__(self):return iter(self.fetchall())

class Postgres:
    def __init__(self,url):
        import psycopg
        from psycopg.rows import dict_row
        self.raw=psycopg.connect(url,row_factory=dict_row,connect_timeout=10)
    def execute(self,sql,params=()):
        import psycopg
        if sql=='BEGIN IMMEDIATE':sql='SELECT pg_advisory_xact_lock(3601701)'
        sql=sql.replace('MAX(suppressed,','GREATEST(suppressed,').replace('MIN(email_allowed,','LEAST(email_allowed,').replace('MIN(whatsapp_allowed,','LEAST(whatsapp_allowed,')
        if 'INSERT OR IGNORE INTO' in sql:
            sql=sql.replace('INSERT OR IGNORE INTO','INSERT INTO')+' ON CONFLICT DO NOTHING'
        sql=sql.replace(' REAL ', ' DOUBLE PRECISION ')
        sql=sql.replace('?', '%s')
        try:return Cursor(self.raw.execute(sql,params))
        except psycopg.IntegrityError as exc:raise sqlite3.IntegrityError('Database constraint violation') from exc
        except psycopg.Error as exc:raise sqlite3.OperationalError('PostgreSQL operation failed') from exc
    def executescript(self,sql):
        for statement in sql.split(';'):
            if statement.strip():self.execute(statement)
    def commit(self):self.raw.commit()
    def rollback(self):self.raw.rollback()
    def close(self):self.raw.close()
