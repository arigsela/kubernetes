# MySQL Backup Docker Image Build Instructions

## Prerequisites
- Docker installed and running
- AWS CLI configured with credentials
- Access to ECR repository: `852893458518.dkr.ecr.us-east-2.amazonaws.com`

## Build and Push Instructions

### Step 1: Navigate to the MySQL directory
```bash
cd /Users/arisela/git/kubernetes/base-apps/mysql
```

### Step 2: Build the Docker image
```bash
docker build -f backup-dockerfile -t mysql-backup:1.0.0 .
```

### Step 3: Tag the image for ECR
```bash
docker tag mysql-backup:1.0.0 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.0
docker tag mysql-backup:1.0.0 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:latest
```

### Step 4: Authenticate Docker to ECR
```bash
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
```

### Step 5: Create ECR repository (if it doesn't exist)
```bash
aws ecr create-repository \
  --repository-name mysql-backup \
  --region us-east-2 \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256
```

If the repository already exists, you'll get an error - that's fine, just continue.

### Step 6: Push the image to ECR
```bash
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.0
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:latest
```

### Step 7: Verify the image was pushed
```bash
aws ecr describe-images \
  --repository-name mysql-backup \
  --region us-east-2
```

## Testing the Image Locally (Optional)

Before deploying to Kubernetes, you can test the image locally:

### Test 1: Verify the script is present
```bash
docker run --rm mysql-backup:1.0.0 cat /usr/local/bin/backup-script.sh
```

### Test 2: Check AWS CLI version
```bash
docker run --rm mysql-backup:1.0.0 aws --version
```

### Test 3: Check MySQL client version
```bash
docker run --rm mysql-backup:1.0.0 mysql --version
```

### Test 4: Run a backup (requires environment variables)
```bash
docker run --rm \
  -e MYSQL_HOST="mysql.mysql.svc.cluster.local" \
  -e MYSQL_USER="root" \
  -e MYSQL_PASSWORD="your-password" \
  -e S3_BUCKET="mysql-backups-asela-cluster" \
  -e AWS_ACCESS_KEY_ID="your-access-key" \
  -e AWS_SECRET_ACCESS_KEY="your-secret-key" \
  -e AWS_REGION="us-east-1" \
  mysql-backup:1.0.0
```

## Quick All-in-One Build & Push Script

```bash
#!/bin/bash
set -e

cd /Users/arisela/git/kubernetes/base-apps/mysql

echo "Building Docker image..."
docker build -f backup-dockerfile -t mysql-backup:1.0.0 .

echo "Tagging for ECR..."
docker tag mysql-backup:1.0.0 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.0
docker tag mysql-backup:1.0.0 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:latest

echo "Authenticating with ECR..."
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com

echo "Creating ECR repository (if needed)..."
aws ecr create-repository \
  --repository-name mysql-backup \
  --region us-east-2 \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 2>/dev/null || echo "Repository already exists"

echo "Pushing to ECR..."
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.0
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:latest

echo "Done! Image pushed to ECR successfully."
```

## Image Details

- **Base Image**: mysql:8.4.0-oraclelinux8
- **Additional Tools**: AWS CLI v2, bc, unzip
- **Entrypoint**: `/usr/local/bin/backup-script.sh`
- **Size**: ~800MB (MySQL base + AWS CLI)

## Updating the Image

When you make changes to the backup script:

1. Rebuild the image with a new version tag:
   ```bash
   docker build -f backup-dockerfile -t mysql-backup:1.0.1 .
   ```

2. Push with the new version:
   ```bash
   docker tag mysql-backup:1.0.1 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.1
   docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/mysql-backup:1.0.1
   ```

3. Update the CronJob manifest to use the new version

## Troubleshooting

### Error: "permission denied while trying to connect to the Docker daemon socket"
Run Docker commands with `sudo` or add your user to the docker group.

### Error: "no basic auth credentials"
Re-authenticate with ECR:
```bash
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
```

### Error: "repository does not exist"
Create the ECR repository first (see Step 5 above).
