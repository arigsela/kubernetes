# Best Practices Guide

## Overview

This guide provides comprehensive best practices for developing applications that use your **asela-test3db** MySQL database effectively and securely.

## Database Design Principles

### Schema Design

#### Normalization Guidelines

```sql
-- Example: Properly normalized user and order tables
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_email (email),
    INDEX idx_created_at (created_at)
);

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    status ENUM('pending', 'processing', 'shipped', 'delivered', 'cancelled') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_user_id (user_id),
    INDEX idx_status_created (status, created_at)
);
```

#### Data Type Best Practices

| Use Case | Recommended Type | Avoid | Reason |
|----------|-----------------|-------|---------|
| **Primary Keys** | `INT AUTO_INCREMENT` or `BIGINT` | `VARCHAR` | Better performance, storage |
| **Timestamps** | `TIMESTAMP` or `DATETIME` | `VARCHAR` dates | Proper sorting, filtering |
| **Money** | `DECIMAL(10,2)` | `FLOAT` | Precision for financial data |
| **Boolean** | `BOOLEAN` or `TINYINT(1)` | `CHAR(1)` | Clear intent, storage efficiency |
| **Enum Values** | `ENUM()` | `VARCHAR` for fixed sets | Constraint enforcement |
| **Text Content** | `TEXT` | `VARCHAR(MAX)` | Appropriate for variable content |

### Indexing Strategy

#### Essential Indexes

```sql
-- Primary key (automatic)
PRIMARY KEY (id)

-- Foreign keys
INDEX idx_fk_user_id (user_id)

-- Frequently queried columns
INDEX idx_email (email)
INDEX idx_status (status)

-- Composite indexes for common query patterns
INDEX idx_user_status_date (user_id, status, created_at)

-- Unique constraints
UNIQUE INDEX idx_unique_email (email)
```

#### Index Optimization

```sql
-- Check index usage
SELECT 
    TABLE_SCHEMA,
    TABLE_NAME,
    INDEX_NAME,
    SEQ_IN_INDEX,
    COLUMN_NAME,
    CARDINALITY
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'asela-test3db'
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

-- Find unused indexes
SELECT * FROM sys.schema_unused_indexes 
WHERE object_schema = 'asela-test3db';
```

## Application Development

### Connection Management

#### Connection Pooling Configuration

=== "Python (SQLAlchemy)"
    ```python
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool
    
    # Production configuration
    engine = create_engine(
        f'mysql+pymysql://{username}:{password}@{host}/{database}',
        pool_size=20,           # Permanent connections
        max_overflow=30,        # Additional connections when busy
        pool_pre_ping=True,     # Validate connections
        pool_recycle=3600,      # Recycle after 1 hour
        echo=False,             # Don't log all SQL in production
        connect_args={
            'charset': 'utf8mb4',
            'ssl': {'ssl_disabled': False},
            'connect_timeout': 30,
            'read_timeout': 30,
            'write_timeout': 30
        }
    )
    ```

=== "Java (HikariCP)"
    ```java
    HikariConfig config = new HikariConfig();
    config.setJdbcUrl("jdbc:mysql://host:3306/database");
    config.setUsername(username);
    config.setPassword(password);
    
    // Pool settings
    config.setMaximumPoolSize(25);
    config.setMinimumIdle(5);
    config.setConnectionTimeout(30000);    // 30 seconds
    config.setIdleTimeout(600000);         // 10 minutes
    config.setMaxLifetime(1800000);        // 30 minutes
    config.setLeakDetectionThreshold(60000); // 1 minute
    
    // Performance settings
    config.addDataSourceProperty("cachePrepStmts", "true");
    config.addDataSourceProperty("prepStmtCacheSize", "250");
    config.addDataSourceProperty("prepStmtCacheSqlLimit", "2048");
    config.addDataSourceProperty("useServerPrepStmts", "true");
    
    HikariDataSource dataSource = new HikariDataSource(config);
    ```

=== "Node.js (mysql2)"
    ```javascript
    const mysql = require('mysql2/promise');
    
    const pool = mysql.createPool({
        host: 'mysql.asela-test3.svc.cluster.local',
        user: 'asela-test3user',
        password: process.env.DB_PASSWORD,
        database: 'asela-test3db',
        
        // Pool configuration
        connectionLimit: 20,
        acquireTimeout: 60000,
        timeout: 60000,
        
        // Connection settings
        ssl: { rejectUnauthorized: true },
        charset: 'utf8mb4',
        timezone: 'Z',
        
        // Reconnection
        reconnect: true,
        reconnectDelay: 2000,
        
        // Performance
        supportBigNumbers: true,
        bigNumberStrings: true
    });
    
    module.exports = pool;
    ```

### Query Best Practices

#### SQL Query Guidelines

```sql
-- ✅ Good: Use specific columns
SELECT id, name, email FROM users WHERE active = 1;

-- ❌ Bad: Select everything
SELECT * FROM users WHERE active = 1;

-- ✅ Good: Use indexes effectively
SELECT * FROM orders 
WHERE user_id = 123 AND status = 'pending' 
ORDER BY created_at DESC 
LIMIT 10;

-- ❌ Bad: Function in WHERE clause
SELECT * FROM orders 
WHERE DATE(created_at) = '2024-01-15';

-- ✅ Good: Range query instead
SELECT * FROM orders 
WHERE created_at >= '2024-01-15 00:00:00' 
  AND created_at < '2024-01-16 00:00:00';
```

#### Parameterized Queries

=== "Python"
    ```python
    # ✅ Secure: Parameterized query
    cursor.execute(
        "SELECT * FROM users WHERE email = %s AND active = %s",
        (email, True)
    )
    
    # ❌ Vulnerable: String concatenation
    cursor.execute(f"SELECT * FROM users WHERE email = '{email}'")
    ```

=== "Java"
    ```java
    // ✅ Secure: PreparedStatement
    String sql = "SELECT * FROM users WHERE email = ? AND active = ?";
    PreparedStatement stmt = connection.prepareStatement(sql);
    stmt.setString(1, email);
    stmt.setBoolean(2, true);
    ResultSet rs = stmt.executeQuery();
    
    // ❌ Vulnerable: String concatenation
    String sql = "SELECT * FROM users WHERE email = '" + email + "'";
    ```

=== "Node.js"
    ```javascript
    // ✅ Secure: Parameterized query
    const [rows] = await pool.execute(
        'SELECT * FROM users WHERE email = ? AND active = ?',
        [email, true]
    );
    
    // ❌ Vulnerable: Template literal
    const sql = `SELECT * FROM users WHERE email = '${email}'`;
    ```

### Transaction Management

#### Transaction Best Practices

```sql
-- ✅ Good: Keep transactions short
START TRANSACTION;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;

-- ❌ Bad: Long-running transaction
START TRANSACTION;
-- ... many operations including user input ...
COMMIT;
```

#### Application Transaction Handling

=== "Python (SQLAlchemy)"
    ```python
    from sqlalchemy.orm import sessionmaker
    
    Session = sessionmaker(bind=engine)
    
    def transfer_money(from_account, to_account, amount):
        session = Session()
        try:
            # Start transaction
            from_acc = session.query(Account).filter_by(id=from_account).with_for_update().first()
            to_acc = session.query(Account).filter_by(id=to_account).with_for_update().first()
            
            if from_acc.balance < amount:
                raise ValueError("Insufficient funds")
            
            from_acc.balance -= amount
            to_acc.balance += amount
            
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    ```

=== "Java (Spring)"
    ```java
    @Service
    @Transactional
    public class AccountService {
        
        @Transactional(isolation = Isolation.READ_COMMITTED)
        public void transferMoney(Long fromAccount, Long toAccount, BigDecimal amount) {
            Account from = accountRepository.findByIdForUpdate(fromAccount);
            Account to = accountRepository.findByIdForUpdate(toAccount);
            
            if (from.getBalance().compareTo(amount) < 0) {
                throw new InsufficientFundsException();
            }
            
            from.setBalance(from.getBalance().subtract(amount));
            to.setBalance(to.getBalance().add(amount));
            
            accountRepository.save(from);
            accountRepository.save(to);
        }
    }
    ```

## Performance Optimization

### Query Optimization

#### Batch Operations

```sql
-- ✅ Good: Batch insert
INSERT INTO products (name, price, category_id) VALUES 
    ('Product 1', 19.99, 1),
    ('Product 2', 29.99, 1),
    ('Product 3', 39.99, 2);

-- ✅ Good: Batch update with CASE
UPDATE products 
SET price = CASE 
    WHEN id = 1 THEN 19.99
    WHEN id = 2 THEN 29.99
    WHEN id = 3 THEN 39.99
    ELSE price
END
WHERE id IN (1, 2, 3);
```

#### Pagination

```sql
-- ✅ Good: Offset-based pagination (small offsets)
SELECT * FROM orders 
ORDER BY id DESC 
LIMIT 20 OFFSET 0;

-- ✅ Better: Cursor-based pagination (large datasets)
SELECT * FROM orders 
WHERE id < 1000 
ORDER BY id DESC 
LIMIT 20;
```

### Caching Strategies

#### Application-Level Caching

=== "Redis Caching"
    ```python
    import redis
    import json
    from datetime import timedelta
    
    redis_client = redis.Redis(host='redis', port=6379, db=0)
    
    def get_user_profile(user_id):
        cache_key = f"user_profile:{user_id}"
        
        # Try cache first
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        
        # Query database
        user = db.query("SELECT * FROM users WHERE id = %s", (user_id,))
        
        # Cache for 1 hour
        redis_client.setex(
            cache_key,
            timedelta(hours=1),
            json.dumps(user, default=str)
        )
        
        return user
    
    def invalidate_user_cache(user_id):
        cache_key = f"user_profile:{user_id}"
        redis_client.delete(cache_key)
    ```

=== "Memcached"
    ```java
    @Service
    public class UserService {
        
        @Autowired
        private MemcachedClient memcachedClient;
        
        @Cacheable(value = "userProfiles", key = "#userId")
        public User getUserProfile(Long userId) {
            return userRepository.findById(userId);
        }
        
        @CacheEvict(value = "userProfiles", key = "#userId")
        public void updateUser(Long userId, User user) {
            userRepository.save(user);
        }
    }
    ```

## Security Best Practices

### Input Validation

```python
from sqlalchemy import text
import re

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def get_user_by_email(email):
    if not validate_email(email):
        raise ValueError("Invalid email format")
    
    # Safe parameterized query
    query = text("SELECT * FROM users WHERE email = :email")
    result = session.execute(query, {"email": email})
    return result.fetchone()
```

### Error Handling

```python
import logging

logger = logging.getLogger(__name__)

def safe_database_operation():
    try:
        # Database operation
        result = session.execute(query)
        session.commit()
        return result
    except SQLAlchemyError as e:
        session.rollback()
        # Log the actual error for debugging
        logger.error(f"Database error: {str(e)}")
        # Return generic error to user
        raise Exception("Database operation failed")
    except Exception as e:
        session.rollback()
        logger.error(f"Unexpected error: {str(e)}")
        raise Exception("Internal server error")
```

### Secrets Management

```python
import os
from kubernetes import client, config

def get_database_password():
    """Retrieve password from Kubernetes secret"""
    try:
        # Load cluster config
        config.load_incluster_config()
        v1 = client.CoreV1Api()
        
        # Read secret
        secret = v1.read_namespaced_secret(
            name="asela-test3-secret",
            namespace="asela-test3"
        )
        
        # Decode password
        password = base64.b64decode(secret.data['DB_PASSWORD']).decode('utf-8')
        return password
    except Exception as e:
        logger.error(f"Failed to retrieve database password: {e}")
        raise Exception("Authentication configuration error")

# Never log passwords
def create_connection():
    password = get_database_password()
    connection_string = f"mysql://{username}:***@{host}/{database}"
    logger.info(f"Connecting to database: {connection_string}")
    return create_engine(f"mysql://{username}:{password}@{host}/{database}")
```

## Monitoring and Observability

### Application Metrics

```python
from prometheus_client import Counter, Histogram, Gauge
import time

# Define metrics
db_queries_total = Counter('db_queries_total', 'Total database queries', ['query_type', 'status'])
db_query_duration = Histogram('db_query_duration_seconds', 'Database query duration')
db_connections_active = Gauge('db_connections_active', 'Active database connections')

def execute_query_with_metrics(query, query_type='select'):
    start_time = time.time()
    try:
        result = session.execute(query)
        db_queries_total.labels(query_type=query_type, status='success').inc()
        return result
    except Exception as e:
        db_queries_total.labels(query_type=query_type, status='error').inc()
        raise e
    finally:
        duration = time.time() - start_time
        db_query_duration.observe(duration)
```

### Health Checks

```python
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/health')
def health_check():
    try:
        # Simple database connectivity check
        result = session.execute(text("SELECT 1"))
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "database": "disconnected",
            "error": "Database connection failed",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

@app.route('/ready')
def readiness_check():
    try:
        # More comprehensive readiness check
        result = session.execute(text("SELECT COUNT(*) FROM users LIMIT 1"))
        return jsonify({
            "status": "ready",
            "database": "ready",
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            "status": "not_ready",
            "database": "not_ready",
            "timestamp": datetime.utcnow().isoformat()
        }), 503
```

## Deployment Practices

### Environment Configuration

```yaml
# Kubernetes deployment example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: asela-test3
  namespace: asela-test3
spec:
  replicas: 3
  selector:
    matchLabels:
      app: asela-test3
  template:
    metadata:
      labels:
        app: asela-test3
    spec:
      serviceAccountName: asela-test3-sa
      containers:
      - name: app
        image: asela-test3:latest
        env:
        - name: DB_HOST
          value: "mysql.asela-test3.svc.cluster.local"
        - name: DB_PORT
          value: "3306"
        - name: DB_NAME
          value: "asela-test3db"
        - name: DB_USER
          value: "asela-test3user"
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: asela-test3-secret
              key: DB_PASSWORD
        # Health checks
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
        # Resource limits
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
```

### Database Migrations

```python
# Example migration script
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create new table
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('preference_key', sa.String(100), nullable=False),
        sa.Column('preference_value', sa.Text, nullable=True),
        sa.Column('created_at', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Index('idx_user_pref_key', 'user_id', 'preference_key', unique=True)
    )

def downgrade():
    op.drop_table('user_preferences')
```

## Testing Strategies

### Database Testing

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture(scope="function")
def db_session():
    # Use test database
    engine = create_engine('mysql://test_user:test_pass@localhost/test_db')
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Setup test data
    yield session
    
    # Cleanup after test
    session.rollback()
    session.close()

def test_user_creation(db_session):
    user = User(name="Test User", email="test@example.com")
    db_session.add(user)
    db_session.commit()
    
    assert user.id is not None
    assert user.created_at is not None

def test_user_query_performance(db_session):
    # Performance test
    start_time = time.time()
    users = db_session.query(User).filter(User.active == True).limit(100).all()
    execution_time = time.time() - start_time
    
    assert execution_time < 0.1  # Should complete in under 100ms
    assert len(users) <= 100
```

## Error Handling and Recovery

### Retry Logic

```python
import time
import random
from functools import wraps

def retry_db_operation(max_retries=3, backoff_base=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as e:
                    if attempt == max_retries - 1:
                        raise e
                    
                    # Exponential backoff with jitter
                    delay = backoff_base ** attempt + random.uniform(0, 1)
                    time.sleep(delay)
                    logger.warning(f"Retrying database operation, attempt {attempt + 1}")
            
        return wrapper
    return decorator

@retry_db_operation(max_retries=3)
def get_user_data(user_id):
    return session.query(User).filter(User.id == user_id).first()
```

### Circuit Breaker Pattern

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
    
    def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            self.reset()
            return result
        except Exception as e:
            self.record_failure()
            raise e
    
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
    
    def reset(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

# Usage
db_circuit_breaker = CircuitBreaker()

def safe_db_query(query):
    return db_circuit_breaker.call(session.execute, query)
```

## Documentation Standards

### Code Documentation

```python
def create_user_account(email: str, name: str, password: str) -> User:
    """
    Create a new user account with validation and security measures.
    
    Args:
        email (str): User's email address (must be unique)
        name (str): User's full name
        password (str): Plain text password (will be hashed)
    
    Returns:
        User: Created user object with generated ID
    
    Raises:
        ValueError: If email format is invalid or already exists
        DatabaseError: If database operation fails
    
    Example:
        >>> user = create_user_account("john@example.com", "John Doe", "secure123")
        >>> print(user.id)
        1
    """
    # Implementation here
    pass
```

### Database Schema Documentation

```sql
-- Table: users
-- Purpose: Store user account information
-- Owner: Platform Team
-- Last Modified: 2024-01-15

CREATE TABLE users (
    -- Primary identifier for user records
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    -- Unique email address for authentication
    -- Format: validated email format
    -- Constraints: UNIQUE, NOT NULL
    email VARCHAR(255) UNIQUE NOT NULL,
    
    -- User's display name
    -- Constraints: NOT NULL, 1-100 characters
    name VARCHAR(100) NOT NULL,
    
    -- Account status flag
    -- Default: TRUE (active account)
    active BOOLEAN DEFAULT TRUE,
    
    -- Record creation timestamp
    -- Auto-populated on INSERT
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Record modification timestamp
    -- Auto-updated on UPDATE
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- Indexes for performance
    INDEX idx_email (email),
    INDEX idx_active_created (active, created_at)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

## Next Steps

- [Security Guide →](security.md)
- [Performance Tuning →](performance.md)
- [Troubleshooting →](troubleshooting.md)
- [Operations Guide →](operations.md)