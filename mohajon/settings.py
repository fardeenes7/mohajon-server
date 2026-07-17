import os
# pyrefly: ignore [missing-import]
import environ
from pathlib import Path
from datetime import timedelta

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

env = environ.Env(
    DEBUG=(bool, False)
)

# Sentry Initialization
SENTRY_DSN = env('SENTRY_DSN', default=None)
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        # Default 10% sampling — set SENTRY_TRACES_SAMPLE_RATE=1.0 for full tracing (expensive)
        traces_sample_rate=float(env('SENTRY_TRACES_SAMPLE_RATE', default='0.1')),
        send_default_pii=True
    )

BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = env('SECRET_KEY', default='django-insecure-default-key-for-dev')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])
WEB_URL = env('WEB_URL', default='http://localhost:3000')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.postgres',
    
    # Third party
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt',
    'corsheaders',
    'drf_spectacular',
    
    # Auth & Social
    'dj_rest_auth',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    
    # Celery Beat
    'django_celery_beat',
    
    # Third-party filters
    'django_filters',

    # Local
    'core',
    'ai',
    'chat',
    'users',
    'shops',
    'orders',
    'webhooks',
    'notifications',
    'compliance',
    'marketing',
    'media.apps.MediaConfig',
    'catalog',
    'billing',
    'accounting',
    'analytics',
    'affiliates',
    'fraud',
    'identity',
]

SITE_ID = 1

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'core.middleware.TenantMiddleware', # Context-aware RLS middleware
]

ROOT_URLCONF = 'mohajon.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mohajon.wsgi.application'

import sys
IS_TESTING = 'test' in sys.argv

DATABASES = {
    'default': {
        **env.db('DATABASE_URL', default='postgres://mohajon:mohajon_password@localhost:5432/mohajon'),
        'CONN_MAX_AGE': env.int('CONN_MAX_AGE', default=60),
    }
}

if IS_TESTING:
    DATABASES['default']['ENGINE'] = 'django.db.backends.postgresql'
    # Bypass PgBouncer for tests to allow creating/connecting to test DBs
    if 'pgbouncer' in DATABASES['default'].get('HOST', ''):
        DATABASES['default']['HOST'] = 'db'
        DATABASES['default']['PORT'] = '5432'
else:
    DATABASES['default']['ENGINE'] = 'django_zero_downtime_migrations.backends.postgres'

# Auth Model
AUTH_USER_MODEL = 'users.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = 'media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'mediafiles')

# ─── S3 / MinIO Media Storage ───────────────────────────────────────────────
# Use AWS_S3_ENDPOINT_URL to switch between local MinIO and real S3:
#   Dev:  AWS_S3_ENDPOINT_URL=http://localhost:9000
#   Prod: leave AWS_S3_ENDPOINT_URL unset (points to real AWS S3)
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='mohajon_minio')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='mohajon_minio_secret')
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='mohajon-media')
AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
AWS_S3_ENDPOINT_URL = env('AWS_S3_ENDPOINT_URL', default='')
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400, public'}
AWS_QUERYSTRING_AUTH = False  # CDN-served URLs should be public
AWS_S3_FILE_OVERWRITE = True   # We overwrite on WebP conversion
CDN_BASE_URL = env('CDN_BASE_URL', default='')

# Use S3 for media storage; static files remain local in dev
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_RATES': {
        'social_oauth': '30/hour',
        'social_publish': '180/hour',
    },
}

# SimpleJWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# REST Auth settings — dj-rest-auth v4+ uses this dict format.
REST_AUTH = {
    'USE_JWT': True,
    'JWT_AUTH_COOKIE': 'mohajon-auth',
    'JWT_AUTH_REFRESH_COOKIE': 'mohajon-refresh-token',
    'JWT_AUTH_HTTPONLY': True,
    'JWT_AUTH_RETURN_EXPIRATION': True,
    'SESSION_LOGIN': False,
    # Prevent dj-rest-auth from trying to create DRF Knox tokens (not used)
    'TOKEN_MODEL': None,
}


# SPECTACULAR
SPECTACULAR_SETTINGS = {
    'TITLE': 'Mohajon SaaS API',
    'DESCRIPTION': 'Modular Monolith Backend',
    'VERSION': '0.1.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# Celery Configuration
# Broker: Redis db 1 (db 0 is the cache/result backend — kept separate to avoid key collisions).
# Previously defaulted to amqp:// (RabbitMQ) — updated to match the broker migration.
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/1')
CELERY_RESULT_BACKEND = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Celery Beat Schedule — Periodic Tasks
CELERY_BEAT_SCHEDULE = {
    'purge-orphaned-media-daily': {
        'task': 'media.tasks.cleanup.purge_orphaned_media',
        'schedule': 60 * 60 * 24,  # Every 24 hours
        'options': {'queue': 'default'},
    },
    'auto-publish-scheduled-products': {
        'task': 'catalog.tasks.auto_publish_scheduled',
        'schedule': 60 * 5,  # Every 5 minutes
        'options': {'queue': 'default'},
    },
    'refresh-social-meta-tokens': {
        'task': 'marketing.tasks.refresh_meta_tokens',
        'schedule': 60 * 60 * 6,  # Every 6 hours
        'options': {'queue': 'default'},
    },
    'sweep-grace-periods-daily': {
        'task': 'billing.tasks.subscription.sweep_grace_periods',
        'schedule': 60 * 60 * 24,  # Every 24 hours
        'options': {'queue': 'default'},
    },
    'sweep-matured-funds-daily': {
        'task': 'accounting.tasks.sweep_matured_funds',
        'schedule': 60 * 60 * 24,  # Every 24 hours
        'options': {'queue': 'default'},
    },
    'sweep-old-messenger-messages': {
        'task': 'chat.tasks.sweep_old_messages',
        'schedule': 60 * 60 * 24,  # Every 24 hours (30-day retention policy)
        'options': {'queue': 'default'},
    },
    'refresh-analytics-views-hourly': {
        'task': 'analytics.tasks.refresh_analytics_materialized_views',
        'schedule': 60 * 60,  # Every hour
        'options': {'queue': 'default'},
    },
    # Custom domain DNS verification — runs the DNS readiness check for all shops
    # with an unverified custom_domain. Effective no-op until custom domain flow is
    # fully wired (Traefik IPs hardcoded in dns_health.py still need updating).
    'validate-pending-custom-domains': {
        'task': 'shops.tasks.domain_validation.validate_pending_custom_domains',
        'schedule': 60 * 10,  # Every 10 minutes
        'options': {'queue': 'default'},
    },
    # Fraud policy sweeps (Phase 4.2): risk decays toward neutral when a profile
    # goes quiet; trust is recomputed from delivered history in the behavioral
    # aggregate. Both idempotent — daily cadence is enough.
    'decay-fraud-risk-scores-daily': {
        'task': 'fraud.tasks.decay_risk_scores',
        'schedule': 60 * 60 * 24,  # Every 24 hours
        'options': {'queue': 'default'},
    },
    'refresh-trust-scores-daily': {
        'task': 'fraud.tasks.refresh_trust_scores',
        'schedule': 60 * 60 * 24,  # Every 24 hours
        'options': {'queue': 'default'},
    },
}
CELERY_TIMEZONE = TIME_ZONE

# ── Redis Cache (django-redis) ───────────────────────────────────────────────
# Used for ConversationBotState, context cache, and stock reservation Lua scripts.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

if IS_TESTING and 'redis' not in CACHES['default']['LOCATION'] and os.path.exists('/.dockerenv'):
    CACHES['default']['LOCATION'] = 'redis://redis:6379/0'

# Auth Configuration
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Allauth / Social Auth
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = 'none' # Trust Google's verification
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ONLY_AUTHENTICATION = True # Disable local email/password login

# Provider specific settings
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
        'APP': {
            'client_id': env('GOOGLE_CLIENT_ID', default=''),
            'secret': env('GOOGLE_CLIENT_SECRET', default=''),
            'key': ''
        }
    }
}

# Email Configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.mailtrap.io')
EMAIL_PORT = env.int('EMAIL_PORT', default=2525)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = '"Mohajon" <noreply@mohajon.io>'

# CORS / CSRF Configuration
# In production: set CORS_ALLOWED_ORIGINS and CSRF_TRUSTED_ORIGINS via env
CORS_ALLOWED_ORIGINS = env.list(
    'CORS_ALLOWED_ORIGINS',
    default=['http://localhost:3000', 'http://127.0.0.1:3000']
)
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = env.list(
    'CSRF_TRUSTED_ORIGINS',
    default=['http://localhost:3000', 'http://127.0.0.1:3000']
)

# Subdomain Routing Security
SUBDOMAIN_BLACKLIST = {
    'admin', 'api', 'app', 'auth', 'mail', 'www', 'help', 'static', 'media', 'public',
    'support', 'dashboard', 'mohajon', 'checkout', 'pay', 'billing', 'docs'
}



# ── Meta OAuth (v0.4 social connect) ───────────────────────────────────────
META_APP_ID = env('META_APP_ID', default='')
META_APP_SECRET = env('META_APP_SECRET', default='')
META_OAUTH_REDIRECT_URI = env('META_OAUTH_REDIRECT_URI', default='')
META_WEBHOOK_VERIFY_TOKEN = env('META_WEBHOOK_VERIFY_TOKEN', default='mohajon_webhook_verify')

# ── OpenAI (v0.6 AI Chatbot) ─────────────────────────────────────────────────
OPENAI_API_KEY = env('OPENAI_API_KEY', default='')
