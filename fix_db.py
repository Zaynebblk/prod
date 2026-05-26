import sqlite3

def fix_database():
    print("Attempting to upgrade database...")
    conn = sqlite3.connect("prodsmart.db")
    cursor = conn.cursor()
    
    try:
        # This adds the missing column to your existing table
        cursor.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
        print("✅ Success: Added 'completed_at' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("ℹ️ Note: Column 'completed_at' already exists.")
        else:
            print(f"❌ Error: {e}")
            
    conn.commit()
    conn.close()

if __name__ == "__main__":
    fix_database()