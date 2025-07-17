# asela-test3 MySQL Database

## Overview

This documentation covers the **asela-test3db** MySQL database instance provisioned for the **asela-test3** application in the **asela-test3** namespace.

!!! info "Database Information"
    - **Database Name**: `asela-test3db`
    - **Environment**: `development`
    - **Owner**: platform-team
    - **System**: examples
    - **Namespace**: `asela-test3`

## Quick Start

### Connection Details

```bash
# Database Host
mysql.asela-test3.svc.cluster.local:3306

# Database Name
asela-test3db

# Username
asela-test3user
```

### Getting the Password

The database password is stored securely in Vault:

```bash
# Using Vault CLI
vault kv get secret/asela-test3/DB_PASSWORD

# Or retrieve from Kubernetes secret
kubectl get secret asela-test3-secret -n asela-test3 -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
```

## User Privileges

This database user has the following privileges:


- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}

- **{{ privilege }}**: {{ 
  "Read data from tables" if privilege == "SELECT" else
  "Insert new data into tables" if privilege == "INSERT" else
  "Modify existing data in tables" if privilege == "UPDATE" else
  "Remove data from tables" if privilege == "DELETE" else
  "Create new tables and databases" if privilege == "CREATE" else
  "Remove tables and databases" if privilege == "DROP" else
  "Create and remove indexes" if privilege == "INDEX" else
  "Modify table structure" if privilege == "ALTER" else
  privilege + " operations"
}}


## Quick Links

| Resource | Link | Description |
|----------|------|-------------|
| 🔐 **Vault Secret** | [https://vault.arigsela.com/ui/vault/secrets/secret/show/asela-test3/DB_PASSWORD](https://vault.arigsela.com/ui/vault/secrets/secret/show/asela-test3/DB_PASSWORD) | Database password storage |
| 📊 **Grafana Dashboard** | [https://grafana.example.com/d/mysql-overview/mysql-database-overview?var-database=asela-test3db&var-namespace=asela-test3](https://grafana.example.com/d/mysql-overview/mysql-database-overview?var-database=asela-test3db&var-namespace=asela-test3) | Database monitoring |
| 🗄️ **phpMyAdmin** | [https://phpmyadmin.example.com/index.php?db=asela-test3db&server=asela-test3-mysql](https://phpmyadmin.example.com/index.php?db=asela-test3db&server=asela-test3-mysql) | Database administration |
| 💬 **Support Channel** | [Slack Channel](https://slack.com/app_redirect?channel=C1234567890) | Get help and support |

## Next Steps

1. **[Connect to the Database →](connection.md)** - Detailed connection instructions
2. **[Set up Authentication →](authentication.md)** - Configure secure access
3. **[Operations Guide →](operations.md)** - Backup, restore, and maintenance
4. **[Troubleshooting →](troubleshooting.md)** - Common issues and solutions

## Architecture

This MySQL database is provisioned using **Crossplane** with the following components:

- **MySQLDatabase**: Custom resource managing the database lifecycle
- **External Secrets**: Automatic password management via Vault
- **ArgoCD Application**: GitOps-based deployment and management
- **Kubernetes Service**: Internal cluster access endpoint

!!! tip "Best Practices"
    - Always use connection pooling in your applications
    - Monitor database performance regularly via Grafana
    - Keep backups up to date using the automated backup system
    - Follow security guidelines when handling database credentials

---

**Generated on**: {{ timestamp }}  
**Template Version**: {{ templateVersion | default("1.0.0") }}