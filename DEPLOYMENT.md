# Xylenix Production Deployment Guide

## Overview
This guide covers deploying Xylenix bot to handle millions of users with high availability and scalability.

## Architecture for Scale

### Production Stack
- **Application**: FastAPI + Python-Telegram-Bot
- **Database**: PostgreSQL with read replicas
- **Cache**: Redis Cluster
- **Queue**: Celery with Redis broker
- **Load Balancer**: Nginx
- **Monitoring**: Prometheus + Grafana
- **Logging**: ELK Stack (Elasticsearch, Logstash, Kibana)

## Infrastructure Requirements

### Minimum Production Setup
- **App Servers**: 2x 4GB RAM, 2 CPU cores
- **Database**: 8GB RAM, 4 CPU cores, SSD storage
- **Redis**: 2GB RAM, 2 CPU cores
- **Load Balancer**: 1GB RAM, 1 CPU core

### High-Scale Setup (1M+ users)
- **App Servers**: 5x 16GB RAM, 8 CPU cores
- **Database**: 32GB RAM, 16 CPU cores, NVMe SSD
- **Redis Cluster**: 3x 8GB RAM, 4 CPU cores
- **Worker Nodes**: 10x 8GB RAM, 4 CPU cores
- **Load Balancers**: 2x 4GB RAM, 2 CPU cores

## Environment Configuration

### Production .env
```bash
# Environment
ENV=production

# Database (PostgreSQL)
DATABASE_URL=postgresql+psycopg://user:pass@db-host:5432/xylenix
DB_POOL_SIZE=50
DB_MAX_OVERFLOW=20

# Redis
REDIS_URL=redis://redis-cluster:6379
CELERY_BROKER_URL=redis://redis-cluster:6379/0
CELERY_RESULT_BACKEND=redis://redis-cluster:6379/1

# Bot Configuration
BOT_TOKEN=your-production-bot-token
BOT_USERNAME=your_bot_username

# Business Configuration
JOIN_FEE_INR=200
TIER1_THRESHOLD=15
TIER1_REWARD_INR=180
TIER2_REWARD_INR=190
MIN_WITHDRAWAL_INR=250
RECEIVER_UPI_ID=your-production-upi

# External APIs
ANTHROPIC_API_KEY=your-production-anthropic-key

# Rate Limiting
RATE_LIMIT_REQUESTS=1000
RATE_LIMIT_WINDOW=60

# Cache TTL
CACHE_TTL_USER=600
CACHE_TTL_REFERRALS=120

# Workers
MAX_WORKERS=10
```

## Docker Deployment

### docker-compose.yml
```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+psycopg://postgres:password@db:5432/xylenix
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    deploy:
      replicas: 3

  bot:
    build: .
    command: python -m src.bot.main
    environment:
      - DATABASE_URL=postgresql+psycopg://postgres:password@db:5432/xylenix
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis

  worker:
    build: .
    command: celery -A src.services.queue.celery_app worker --loglevel=info
    environment:
      - DATABASE_URL=postgresql+psycopg://postgres:password@db:5432/xylenix
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    deploy:
      replicas: 5

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=xylenix
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - app

volumes:
  postgres_data:
  redis_data:
```

### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Default command
CMD ["uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Database Optimization

### PostgreSQL Configuration
```sql
-- Create indexes for performance
CREATE INDEX CONCURRENTLY idx_users_referred_verified ON users(referred_by, is_verified);
CREATE INDEX CONCURRENTLY idx_users_joined_verified ON users(joined_at, is_verified);
CREATE INDEX CONCURRENTLY idx_txn_user_status_created ON transactions(user_id, status, created_at);
CREATE INDEX CONCURRENTLY idx_withdrawal_status_requested ON withdrawal_requests(status, requested_at);

-- Partitioning for large tables
CREATE TABLE transactions_2024 PARTITION OF transactions
FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

-- Connection pooling settings
ALTER SYSTEM SET max_connections = 200;
ALTER SYSTEM SET shared_buffers = '8GB';
ALTER SYSTEM SET effective_cache_size = '24GB';
ALTER SYSTEM SET work_mem = '256MB';
```

## Monitoring Setup

### Prometheus Configuration
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'xylenix-app'
    static_configs:
      - targets: ['app:8000']
    metrics_path: '/metrics'

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres-exporter:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
```

### Key Metrics to Monitor
- Request rate and latency
- Database connection pool usage
- Queue length and processing time
- Memory and CPU usage
- Error rates
- User registration and verification rates

## Scaling Strategies

### Horizontal Scaling
1. **App Servers**: Use load balancer to distribute traffic
2. **Workers**: Scale Celery workers based on queue length
3. **Database**: Implement read replicas for read-heavy operations

### Vertical Scaling
1. **Database**: Increase CPU and RAM for better performance
2. **Cache**: More RAM for Redis to cache more data
3. **Workers**: More CPU cores for AI processing

### Performance Optimizations
1. **Database Connection Pooling**: Use PgBouncer
2. **Caching Strategy**: Cache user data, referral stats
3. **Async Processing**: All I/O operations async
4. **Rate Limiting**: Prevent abuse and ensure fair usage

## Security Considerations

### Application Security
- Use environment variables for secrets
- Implement proper authentication for admin endpoints
- Validate all user inputs
- Use HTTPS in production
- Regular security updates

### Database Security
- Use connection pooling with authentication
- Enable SSL/TLS for database connections
- Regular backups with encryption
- Monitor for suspicious queries

## Backup and Recovery

### Database Backups
```bash
# Daily full backup
pg_dump -h localhost -U postgres xylenix | gzip > backup_$(date +%Y%m%d).sql.gz

# Point-in-time recovery setup
ALTER SYSTEM SET archive_mode = on;
ALTER SYSTEM SET archive_command = 'cp %p /var/lib/postgresql/archive/%f';
```

### Redis Persistence
```bash
# Enable AOF and RDB
redis-cli CONFIG SET save "900 1 300 10 60 10000"
redis-cli CONFIG SET appendonly yes
```

## Deployment Commands

### Initial Setup
```bash
# 1. Clone repository
git clone https://github.com/your-org/xylenix.git
cd xylenix

# 2. Set up environment
cp .env.example .env
# Edit .env with production values

# 3. Build and start services
docker-compose up -d

# 4. Run database migrations
docker-compose exec app python -c "from src.db.init_db import init_db; init_db()"
```

### Updates
```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild and restart
docker-compose build
docker-compose up -d

# 3. Run any new migrations if needed
```

## Monitoring and Alerts

### Critical Alerts
- Database connection failures
- High error rates (>5%)
- Queue backlog (>1000 items)
- Memory usage >90%
- Disk space <10%

### Performance Alerts
- Response time >2 seconds
- Database query time >1 second
- Worker processing time >30 seconds

## Troubleshooting

### Common Issues
1. **High Memory Usage**: Increase worker memory limits
2. **Slow Database**: Add indexes, optimize queries
3. **Queue Backlog**: Scale workers horizontally
4. **Rate Limiting**: Adjust limits based on usage patterns

### Logs to Check
- Application logs: `/var/log/xylenix/app.log`
- Database logs: PostgreSQL logs
- Worker logs: Celery worker logs
- Nginx access logs: `/var/log/nginx/access.log`

This deployment guide ensures your Xylenix bot can handle millions of users with high availability and performance.
