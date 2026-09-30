"""
Django settings for the fuel route planner.

Configuration that differs between machines (secrets, API keys) is read
from environment variables, optionally loaded from a local `.env` file.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-key-change-me-in-production",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "routes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Routing responses are cached on disk so repeated trips (and the map page,
# which reuses the API's result) never hit the external routing API again.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": BASE_DIR / ".cache",
        "TIMEOUT": 60 * 60 * 24,
    }
}

if sys.argv[1:2] == ["test"]:  # tests get a fresh in-memory cache every run
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Django's default ("same-origin") strips the Referer header, and the
# OpenStreetMap tile servers reject tile requests that don't send one.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}


# --- Trip planning -----------------------------------------------------------

# OpenRouteService key (free at openrouteservice.org). When it is not set the
# app falls back to the public OSRM demo server, which needs no key, so the
# project still runs out of the box.
ORS_API_KEY = os.getenv("ORS_API_KEY", "").strip()
ROUTING_TIMEOUT_SECONDS = float(os.getenv("ROUTING_TIMEOUT_SECONDS", "20"))

VEHICLE_RANGE_MILES = float(os.getenv("VEHICLE_RANGE_MILES", "500"))
VEHICLE_MPG = float(os.getenv("VEHICLE_MPG", "10"))

# How far a station may be from the route and still count as "on the way".
# Stations are geocoded to their city, so this absorbs the gap between a
# city's center and the highway exit. If no feasible plan exists within the
# first radius, the planner retries with the wider one (no extra API calls).
STATION_SEARCH_RADII_MILES = [float(r) for r in os.getenv("STATION_SEARCH_RADII_MILES", "10,25").split(",")]

# The optimizer minimizes fuel cost + this amount per stop, so it won't plan a
# stop that saves only a few cents. The reported cost is the fuel bill only.
# Set to 0 for the strictly cheapest plan, however many stops it takes.
STOP_PENALTY_USD = float(os.getenv("STOP_PENALTY_USD", "10"))
