# Performance Tuning Guide

## Overview

This guide provides performance optimization recommendations for your **asela-test3db** MySQL database instance.

## Performance Monitoring

### Key Performance Indicators

Monitor these metrics in your [Grafana Dashboard](https://grafana.example.com/d/mysql-overview/mysql-database-overview?var-database=asela-test3db&var-namespace=asela-test3):

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| **Query Response Time** | < 100ms | 100ms - 1s | > 1s |
| **Queries Per Second** | Varies | - | > 80% capacity |
| **Connection Usage** | < 70% | 70-85% | > 85% |
| **Buffer Pool Hit Ratio** | > 99% | 95-99% | < 95% |
| **Disk I/O Wait** | < 10% | 10-20% | > 20% |
| **Lock Wait Time** | < 1ms | 1-10ms | > 10ms |

### Performance Schema Queries

#### Top Slow Queries

```sql
-- Most time-consuming queries
SELECT 
    DIGEST_TEXT as query,
    COUNT_STAR as exec_count,
    ROUND(AVG_TIMER_WAIT/1000000000, 3) as avg_exec_time_sec,
    ROUND(SUM_TIMER_WAIT/1000000000, 3) as total_exec_time_sec,
    ROUND(AVG_ROWS_EXAMINED, 0) as avg_rows_examined
FROM performance_schema.events_statements_summary_by_digest 
WHERE SCHEMA_NAME = 'asela-test3db'
ORDER BY SUM_TIMER_WAIT DESC 
LIMIT 10;
```

#### Table I/O Statistics

```sql
-- Tables with highest I/O
SELECT 
    OBJECT_SCHEMA,
    OBJECT_NAME,
    COUNT_READ,
    COUNT_WRITE,
    SUM_TIMER_READ/1000000000 as read_time_sec,
    SUM_TIMER_WRITE/1000000000 as write_time_sec
FROM performance_schema.table_io_waits_summary_by_table
WHERE OBJECT_SCHEMA = 'asela-test3db'
ORDER BY SUM_TIMER_READ + SUM_TIMER_WRITE DESC
LIMIT 10;
```

#### Index Usage Analysis

```sql
-- Unused indexes
SELECT 
    OBJECT_SCHEMA,
    OBJECT_NAME,
    INDEX_NAME
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE OBJECT_SCHEMA = 'asela-test3db'
    AND INDEX_NAME IS NOT NULL
    AND COUNT_STAR = 0
ORDER BY OBJECT_SCHEMA, OBJECT_NAME;
```

## Configuration Optimization

### Memory Settings

Current recommended settings for your workload:

```sql
-- Check current buffer pool size
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';

-- Recommended: 70-80% of available memory
-- For 4GB RAM: innodb_buffer_pool_size = 3G
-- For 8GB RAM: innodb_buffer_pool_size = 6G
```

### Connection Settings

```sql
-- Connection limits
SHOW VARIABLES LIKE 'max_connections';
SHOW VARIABLES LIKE 'max_user_connections';

-- Recommended connection pool settings for applications:
-- Development: 5-10 connections
-- Production: 20-50 connections (per application instance)
```

### Query Cache (MySQL 5.7 and earlier)

```sql
-- Query cache settings (deprecated in MySQL 8.0)
SHOW VARIABLES LIKE 'query_cache%';

-- For MySQL 8.0, use application-level caching instead
```

## Index Optimization

### Index Best Practices

1. **Cardinality**: High cardinality columns first in composite indexes
2. **Selectivity**: Most selective columns first
3. **Query Patterns**: Design indexes for your specific queries

### Index Analysis

#### Find Missing Indexes

```sql
-- Queries with full table scans
SELECT 
    OBJECT_SCHEMA,
    OBJECT_NAME,
    COUNT_READ as full_scans
FROM performance_schema.table_io_waits_summary_by_table
WHERE OBJECT_SCHEMA = 'asela-test3db'
    AND COUNT_READ > 1000
ORDER BY COUNT_READ DESC;
```

#### Duplicate Indexes

```sql
-- Find potentially duplicate indexes
SELECT 
    TABLE_SCHEMA,
    TABLE_NAME,
    GROUP_CONCAT(INDEX_NAME) as indexes,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'asela-test3db'
GROUP BY TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
HAVING COUNT(*) > 1;
```

### Index Recommendations

=== "E-commerce Schema"
    ```sql
    -- Product search
    CREATE INDEX idx_products_category_price ON products(category_id, price);
    CREATE INDEX idx_products_name_fulltext ON products(name) USING FULLTEXT;
    
    -- Order queries
    CREATE INDEX idx_orders_user_date ON orders(user_id, created_at);
    CREATE INDEX idx_orders_status_date ON orders(status, created_at);
    ```

=== "User Management"
    ```sql
    -- User lookups
    CREATE UNIQUE INDEX idx_users_email ON users(email);
    CREATE INDEX idx_users_active_created ON users(active, created_at);
    
    -- Session management
    CREATE INDEX idx_sessions_user_expires ON sessions(user_id, expires_at);
    CREATE INDEX idx_sessions_token ON sessions(token);
    ```

=== "Analytics Schema"
    ```sql
    -- Time-series data
    CREATE INDEX idx_events_time_type ON events(timestamp, event_type);
    CREATE INDEX idx_events_user_time ON events(user_id, timestamp);
    
    -- Aggregation queries
    CREATE INDEX idx_daily_stats_date ON daily_stats(date);
    ```

## Query Optimization

### Query Performance Tips

1. **Use EXPLAIN** to understand query execution plans
2. **Avoid SELECT \*** - specify only needed columns
3. **Use LIMIT** for large result sets
4. **Optimize WHERE clauses** - most selective conditions first
5. **Use appropriate data types** - smaller is usually faster

### Query Analysis

#### EXPLAIN Query Plans

```sql
-- Analyze query execution plan
EXPLAIN FORMAT=JSON 
SELECT u.name, COUNT(o.id) as order_count
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.active = 1
GROUP BY u.id
ORDER BY order_count DESC
LIMIT 10;
```

#### Query Rewriting Examples

=== "Inefficient Query"
    ```sql
    -- Slow: Using function in WHERE clause
    SELECT * FROM orders 
    WHERE DATE(created_at) = '2024-01-15';
    ```

=== "Optimized Query"
    ```sql
    -- Fast: Using range query
    SELECT * FROM orders 
    WHERE created_at >= '2024-01-15 00:00:00' 
      AND created_at < '2024-01-16 00:00:00';
    ```

=== "Inefficient Subquery"
    ```sql
    -- Slow: Correlated subquery
    SELECT * FROM products p
    WHERE price > (
        SELECT AVG(price) 
        FROM products p2 
        WHERE p2.category_id = p.category_id
    );
    ```

=== "Optimized JOIN"
    ```sql
    -- Fast: JOIN with window function
    SELECT p.*
    FROM products p
    JOIN (
        SELECT category_id, AVG(price) as avg_price
        FROM products
        GROUP BY category_id
    ) cat_avg ON p.category_id = cat_avg.category_id
    WHERE p.price > cat_avg.avg_price;
    ```

## Application-Level Optimization

### Connection Pooling

Configure connection pooling in your applications:

=== "Python (SQLAlchemy)"
    ```python
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool
    
    engine = create_engine(
        'mysql+pymysql://user:pass@host/db',
        poolclass=QueuePool,
        pool_size=20,          # Number of persistent connections
        max_overflow=10,       # Additional connections when needed
        pool_pre_ping=True,    # Validate connections before use
        pool_recycle=3600,     # Recycle connections every hour
    )
    ```

=== "Java (HikariCP)"
    ```java
    HikariConfig config = new HikariConfig();
    config.setJdbcUrl("jdbc:mysql://host:3306/db");
    config.setUsername("user");
    config.setPassword("pass");
    config.setMaximumPoolSize(20);        // Maximum pool size
    config.setMinimumIdle(5);             // Minimum idle connections
    config.setConnectionTimeout(30000);   // 30 seconds
    config.setIdleTimeout(600000);        // 10 minutes
    config.setMaxLifetime(1800000);       // 30 minutes
    config.setLeakDetectionThreshold(60000); // 1 minute
    
    HikariDataSource dataSource = new HikariDataSource(config);
    ```

=== "Node.js (mysql2)"
    ```javascript
    const mysql = require('mysql2');
    
    const pool = mysql.createPool({
        host: 'mysql.namespace.svc.cluster.local',
        user: 'username',
        password: 'password',
        database: 'database',
        connectionLimit: 20,      // Maximum connections
        acquireTimeout: 60000,    // Connection acquisition timeout
        timeout: 60000,           // Query timeout
        reconnect: true,          // Automatic reconnection
        ssl: { rejectUnauthorized: true }
    });
    
    // Use promises
    const promisePool = pool.promise();
    ```

### Caching Strategies

#### Application-Level Caching

=== "Redis Caching"
    ```python
    import redis
    import json
    from datetime import timedelta
    
    redis_client = redis.Redis(host='redis', port=6379, db=0)
    
    def get_user_with_cache(user_id):
        cache_key = f"user:{user_id}"
        
        # Try cache first
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
        
        # Query database
        user = query_database(user_id)
        
        # Cache for 1 hour
        redis_client.setex(
            cache_key, 
            timedelta(hours=1), 
            json.dumps(user)
        )
        
        return user
    ```

=== "Application Cache"
    ```java
    @Service
    public class UserService {
        
        @Cacheable(value = "users", key = "#userId")
        public User getUserById(Long userId) {
            return userRepository.findById(userId);
        }
        
        @CacheEvict(value = "users", key = "#user.id")
        public User updateUser(User user) {
            return userRepository.save(user);
        }
    }
    ```

### Batch Operations

#### Bulk Inserts

```sql
-- Instead of multiple single inserts
INSERT INTO products (name, price, category_id) VALUES 
    ('Product 1', 19.99, 1),
    ('Product 2', 29.99, 1),
    ('Product 3', 39.99, 2),
    -- ... up to 1000 rows per batch
    ('Product N', 49.99, 3);

-- Use transactions for consistency
START TRANSACTION;
INSERT INTO products (name, price, category_id) VALUES 
    ('Product 1', 19.99, 1),
    ('Product 2', 29.99, 1);
-- ... more inserts
COMMIT;
```

#### Bulk Updates

```sql
-- Use CASE statements for bulk updates
UPDATE products 
SET price = CASE 
    WHEN id = 1 THEN 19.99
    WHEN id = 2 THEN 29.99
    WHEN id = 3 THEN 39.99
    ELSE price
END
WHERE id IN (1, 2, 3);
```

## Storage Optimization

### Data Types

Choose appropriate data types for better performance:

```sql
-- Use appropriate integer sizes
TINYINT    -- 1 byte: -128 to 127
SMALLINT   -- 2 bytes: -32,768 to 32,767
MEDIUMINT  -- 3 bytes: -8,388,608 to 8,388,607
INT        -- 4 bytes: -2,147,483,648 to 2,147,483,647
BIGINT     -- 8 bytes: -9,223,372,036,854,775,808 to 9,223,372,036,854,775,807

-- Use VARCHAR instead of CHAR for variable-length strings
VARCHAR(255) -- Variable length, 1-255 characters
TEXT         -- Up to 65,535 characters
MEDIUMTEXT   -- Up to 16,777,215 characters

-- Use appropriate date/time types
DATE         -- Date only: YYYY-MM-DD
DATETIME     -- Date and time: YYYY-MM-DD HH:MM:SS
TIMESTAMP    -- Unix timestamp (auto-updating)
```

### Table Partitioning

For large tables, consider partitioning:

```sql
-- Partition by date range
CREATE TABLE events (
    id BIGINT AUTO_INCREMENT,
    event_time DATETIME NOT NULL,
    event_type VARCHAR(50),
    user_id INT,
    data JSON,
    PRIMARY KEY (id, event_time)
)
PARTITION BY RANGE (TO_DAYS(event_time)) (
    PARTITION p2023 VALUES LESS THAN (TO_DAYS('2024-01-01')),
    PARTITION p2024_q1 VALUES LESS THAN (TO_DAYS('2024-04-01')),
    PARTITION p2024_q2 VALUES LESS THAN (TO_DAYS('2024-07-01')),
    PARTITION p2024_q3 VALUES LESS THAN (TO_DAYS('2024-10-01')),
    PARTITION p2024_q4 VALUES LESS THAN (TO_DAYS('2025-01-01')),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);
```

## Monitoring Performance

### Real-time Monitoring

```bash
# Monitor MySQL processlist
kubectl exec -n asela-test3 \
  $(kubectl get pods -n asela-test3 -l app=mysql -o jsonpath='{.items[0].metadata.name}') -- \
  mysql -u root -p$MYSQL_ROOT_PASSWORD -e "SHOW PROCESSLIST;"

# Monitor system resources
kubectl top pod -n asela-test3 -l app=mysql
```

### Performance Reports

Generate weekly performance reports:

```sql
-- Weekly query performance summary
SELECT 
    DATE(FROM_UNIXTIME(FIRST_SEEN)) as week_start,
    COUNT_STAR as total_executions,
    ROUND(AVG_TIMER_WAIT/1000000000, 3) as avg_execution_time,
    ROUND(SUM_TIMER_WAIT/1000000000, 3) as total_execution_time
FROM performance_schema.events_statements_summary_by_digest
WHERE FIRST_SEEN >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY))
GROUP BY DATE(FROM_UNIXTIME(FIRST_SEEN))
ORDER BY week_start;
```

## Troubleshooting Performance Issues

### Common Performance Problems

1. **Missing Indexes**: Use EXPLAIN to identify table scans
2. **Lock Contention**: Monitor SHOW ENGINE INNODB STATUS
3. **Memory Pressure**: Check buffer pool hit ratio
4. **Disk I/O**: Monitor disk wait times
5. **Network Latency**: Check connection times

### Performance Debugging Tools

```sql
-- Enable performance schema (MySQL 8.0)
UPDATE performance_schema.setup_instruments 
SET ENABLED = 'YES', TIMED = 'YES' 
WHERE NAME LIKE '%statement%';

-- Monitor current queries
SELECT * FROM performance_schema.events_statements_current;

-- Check for locks
SELECT * FROM performance_schema.data_locks;
```

## Next Steps

- [Troubleshooting Guide →](troubleshooting.md)
- [Security Best Practices →](security.md)
- [Operations Guide →](operations.md)