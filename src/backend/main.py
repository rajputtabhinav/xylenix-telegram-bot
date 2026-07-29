import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from src.db.init_db import init_db
from src.services.cache import cache
from src.config import settings
from src.backend.routes import users, verification, withdrawals, admin

# Metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration')

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
	"""Application lifespan management"""
	logger.info("Starting Xylenix Backend...")
	
	# Initialize database
	try:
		init_db()
		logger.info("Database initialized")
	except Exception as e:
		logger.error(f"Database initialization failed: {e}")
		raise
	
	# Initialize cache
	try:
		await cache.connect()
		logger.info("Cache service initialized")
	except Exception as e:
		logger.warning(f"Cache initialization failed: {e}")
	
	yield
	
	# Cleanup
	logger.info("Shutting down Xylenix Backend...")
	await cache.disconnect()

app = FastAPI(
	title="Xylenix Backend",
	description="Telegram referral bot backend with AI verification",
	version="1.0.0",
	lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"] if settings.env == "development" else [],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware for metrics and logging
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
	start_time = time.time()
	
	response = await call_next(request)
	
	# Record metrics
	duration = time.time() - start_time
	REQUEST_DURATION.observe(duration)
	REQUEST_COUNT.labels(
		method=request.method,
		endpoint=request.url.path,
		status=response.status_code
	).inc()
	
	# Log request
	logger.info(
		f"{request.method} {request.url.path} - {response.status_code} - {duration:.3f}s"
	)
	
	return response

# Include routers
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(verification.router, prefix="/api/v1/verification", tags=["verification"])
app.include_router(withdrawals.router, prefix="/api/v1/withdrawals", tags=["withdrawals"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])

@app.get("/health")
@limiter.limit("100/minute")
async def health(request: Request):
	"""Health check endpoint"""
	return JSONResponse({
		"status": "ok",
		"timestamp": time.time(),
		"version": "1.0.0",
		"environment": settings.env
	})

@app.get("/metrics")
async def metrics():
	"""Prometheus metrics endpoint"""
	return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
async def root():
	"""Root endpoint"""
	return {
		"message": "Xylenix Backend API",
		"version": "1.0.0",
		"docs": "/docs",
		"health": "/health"
	}
