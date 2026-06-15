import asyncio

from app.db.database import init_db

async def main():
    await init_db()
    print("Database created successfully!")

asyncio.run(main())