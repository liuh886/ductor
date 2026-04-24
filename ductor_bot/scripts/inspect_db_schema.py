import sqlite3
import os

db_path = r'C:\Users\ZOZN109\.ductor\state.db'
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_fragments'")
schema = cursor.fetchone()
if schema:
    print(schema[0])
else:
    print("Table memory_fragments not found.")
conn.close()
