from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
from app.config import settings
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.database import get_db
from app.services import user_service

security = HTTPBearer()

# Configure the JWKS client to fetch Supabase public keys for asymmetric verification
jwks_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
jwks_client = PyJWKClient(
    jwks_url,
    # The .well-known endpoint is fully public and doesn't require an apikey header
    cache_keys=True
)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """
    FastAPI dependency that verifies a Supabase JWT token using asymmetric JWKS.
    Also ensures the user exists in the local MongoDB.
    Returns a dict with 'uid' and 'email'.
    """
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        decoded_token = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "HS256"],
            audience="authenticated"
        )
        
        uid = decoded_token.get("sub")
        email = decoded_token.get("email", "")
        user_metadata = decoded_token.get("user_metadata", {})
        name = user_metadata.get("full_name", user_metadata.get("name", ""))
        
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid token: missing subject (sub)")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Expired authentication token")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

    # Sync user to MongoDB
    user = await user_service.find_or_create_user(db, uid=uid, email=email, name=name)

    return {
        "uid": uid,
        "email": email,
        "name": name,
        "role": user.get("role", "user")
    }

async def get_admin_user(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """Dependency that requires the current user to be an admin."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
