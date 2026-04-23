from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    shopify_store: str
    shopify_access_token: str

    model_config = {"env_file": ".env"}


settings = Settings()
