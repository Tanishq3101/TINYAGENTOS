# test_day2.py

from infrastructure.config import get_settings
from infrastructure.logging import logger
from infrastructure.auth import hash_password, verify_password, create_access_token

settings = get_settings()

print(settings.APP_NAME)

logger.info("Logger working")

pwd = hash_password("test123")
print("Password valid:", verify_password("test123", pwd))

token = create_access_token({"user": "admin"})
print("Token:", token)