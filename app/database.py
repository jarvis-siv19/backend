from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_to_mongo():
    """Initialize MongoDB connection on app startup."""
    global _client, _db
    _client = AsyncIOMotorClient(settings.MONGODB_URI)
    _db = _client[settings.DB_NAME]
    # Verify connection
    await _client.admin.command("ping")
    print(f"Connected to MongoDB: {settings.DB_NAME}")


async def close_mongo_connection():
    """Close MongoDB connection on app shutdown."""
    global _client
    if _client:
        _client.close()
        print("MongoDB connection closed")


def get_db() -> AsyncIOMotorDatabase:
    """Get the database instance. Used as a FastAPI dependency."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongo() first.")
    return _db
