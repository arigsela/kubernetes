# MySQL Crossplane Composition Implementation Plan

## Overview
This document outlines the implementation of a Crossplane Composition for MySQL database provisioning, transforming the current direct resource approach into a reusable, templated solution.

## Current State Analysis

### Existing Resources
Currently, MySQL resources are created directly:
- **User**: `mysql.sql.crossplane.io/v1alpha1` in namespace `chores-backend`
- **Database**: `mysql.sql.crossplane.io/v1alpha1` in namespace `chores-tracker`
- **Grant**: `mysql.sql.crossplane.io/v1alpha1` in namespace `chores-tracker`

### Dependencies
- Provider-SQL v0.9.0
- MySQL ProviderConfig: `mysql-provider`
- External Secrets for credentials

## Phase 1: Create XRD and Composition

### 1.1 CompositeResourceDefinition (XRD)

Create file: `base-apps/crossplane-mysql/composition-xrd.yaml`

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata:
  name: xmysqldatabases.platform.io
spec:
  group: platform.io
  names:
    kind: XMySQLDatabase
    plural: xmysqldatabases
  claimNames:
    kind: MySQLDatabase
    plural: mysqldatabases
  connectionSecretKeys:
    - username
    - password
    - database
    - endpoint
    - port
  versions:
  - name: v1alpha1
    served: true
    referenceable: true
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              parameters:
                type: object
                properties:
                  databaseName:
                    type: string
                    description: "Name of the MySQL database"
                    pattern: "^[a-zA-Z][a-zA-Z0-9_]{0,63}$"
                  username:
                    type: string
                    description: "Name of the MySQL user"
                    pattern: "^[a-zA-Z][a-zA-Z0-9_-]{0,31}$"
                  userNamespace:
                    type: string
                    description: "Namespace where the user resource will be created"
                    default: "default"
                  databaseNamespace:
                    type: string
                    description: "Namespace where the database and grant resources will be created"
                    default: "default"
                  privileges:
                    type: array
                    description: "List of privileges to grant"
                    default: ["ALL"]
                    items:
                      type: string
                      enum:
                        - "ALL"
                        - "SELECT"
                        - "INSERT"
                        - "UPDATE"
                        - "DELETE"
                        - "CREATE"
                        - "DROP"
                        - "ALTER"
                        - "INDEX"
                        - "CREATE VIEW"
                        - "SHOW VIEW"
                        - "TRIGGER"
                        - "EXECUTE"
                  passwordSecretRef:
                    type: object
                    description: "Reference to secret containing password"
                    properties:
                      name:
                        type: string
                      key:
                        type: string
                        default: "password"
                      namespace:
                        type: string
                    required:
                      - name
                      - namespace
                required:
                  - databaseName
                  - username
            required:
              - parameters
          status:
            type: object
            properties:
              ready:
                type: boolean
                description: "Whether all resources are ready"
```

### 1.2 Composition

Create file: `base-apps/crossplane-mysql/composition.yaml`

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: xmysqldatabase.platform.io
  labels:
    crossplane.io/xrd: xmysqldatabases.platform.io
    provider: mysql
spec:
  writeConnectionSecretsToNamespace: crossplane-system
  compositeTypeRef:
    apiVersion: platform.io/v1alpha1
    kind: XMySQLDatabase
  
  resources:
    # MySQL User
    - name: mysql-user
      base:
        apiVersion: mysql.sql.crossplane.io/v1alpha1
        kind: User
        spec:
          providerConfigRef:
            name: mysql-provider
          forProvider:
            passwordSecretRef:
              key: password
      patches:
        # Set user name
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.username
          toFieldPath: metadata.name
        
        # Set user namespace
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.userNamespace
          toFieldPath: metadata.namespace
        
        # Password secret reference
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.passwordSecretRef.name
          toFieldPath: spec.forProvider.passwordSecretRef.name
        
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.passwordSecretRef.key
          toFieldPath: spec.forProvider.passwordSecretRef.key
        
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.passwordSecretRef.namespace
          toFieldPath: spec.forProvider.passwordSecretRef.namespace
        
        # Add external-name annotation for provider
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.username
          toFieldPath: metadata.annotations[crossplane.io/external-name]
      
      readinessChecks:
        - type: MatchString
          fieldPath: status.atProvider.ready
          matchString: "True"
    
    # MySQL Database
    - name: mysql-database
      base:
        apiVersion: mysql.sql.crossplane.io/v1alpha1
        kind: Database
        spec:
          providerConfigRef:
            name: mysql-provider
          forProvider: {}
      patches:
        # Set database name
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.databaseName
          toFieldPath: metadata.name
        
        # Set database namespace
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.databaseNamespace
          toFieldPath: metadata.namespace
        
        # Add external-name annotation
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.databaseName
          toFieldPath: metadata.annotations[crossplane.io/external-name]
      
      readinessChecks:
        - type: MatchString
          fieldPath: status.atProvider.ready
          matchString: "True"
    
    # MySQL Grant
    - name: mysql-grant
      base:
        apiVersion: mysql.sql.crossplane.io/v1alpha1
        kind: Grant
        spec:
          providerConfigRef:
            name: mysql-provider
          forProvider:
            privileges: []
      patches:
        # Generate unique grant name
        - type: CombineFromComposite
          toFieldPath: metadata.name
          combine:
            variables:
              - fromFieldPath: spec.parameters.username
              - fromFieldPath: spec.parameters.databaseName
            strategy: string
            string:
              fmt: "%s-%s-grant"
        
        # Set grant namespace (same as database)
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.databaseNamespace
          toFieldPath: metadata.namespace
        
        # Set user reference
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.username
          toFieldPath: spec.forProvider.user
        
        # Set database reference
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.databaseName
          toFieldPath: spec.forProvider.database
        
        # Set privileges
        - type: FromCompositeFieldPath
          fromFieldPath: spec.parameters.privileges
          toFieldPath: spec.forProvider.privileges
      
      readinessChecks:
        - type: MatchString
          fieldPath: status.atProvider.ready
          matchString: "True"
```

### 1.3 Deployment Strategy

Since the existing `crossplane-mysql` ArgoCD application already monitors the `/base-apps/crossplane-mysql/` directory, we can simply add the Composition and XRD files there:

Place the files in:
- `base-apps/crossplane-mysql/composition-xrd.yaml` (contains the XRD)
- `base-apps/crossplane-mysql/composition.yaml` (contains the Composition)

The existing ArgoCD application will automatically sync these resources.

## Phase 2: Test with New Application

### 2.1 Test Application Claim

Create file: `base-apps/test-mysql-app/claim.yaml`

```yaml
apiVersion: platform.io/v1alpha1
kind: MySQLDatabase
metadata:
  name: test-app-db
  namespace: test-app
spec:
  parameters:
    databaseName: testapp_db
    username: testapp_user
    userNamespace: test-app-backend
    databaseNamespace: test-app
    privileges:
      - "SELECT"
      - "INSERT"
      - "UPDATE"
      - "DELETE"
      - "CREATE"
      - "DROP"
      - "INDEX"
    passwordSecretRef:
      name: test-app-db-secret
      key: DB_PASSWORD
      namespace: test-app
  writeConnectionSecretToRef:
    name: test-app-db-connection
```

### 2.2 Test Application Secret

Create file: `base-apps/test-mysql-app/external-secret.yaml`

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: test-app-db-secret
  namespace: test-app
spec:
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: test-app-db-secret
    creationPolicy: Owner
  data:
    - secretKey: DB_PASSWORD
      remoteRef:
        key: secret/data/test-app/database
        property: password
```

### 2.3 Test Application ArgoCD App

Create file: `base-apps/test-mysql-app.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: test-mysql-app
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/test-mysql-app
  destination:
    server: https://kubernetes.default.svc
    namespace: test-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

## Implementation Steps

### Phase 1 Steps
1. Add the XRD and Composition files to the existing crossplane-mysql directory:
   ```bash
   # Files go directly in:
   base-apps/crossplane-mysql/composition-xrd.yaml
   base-apps/crossplane-mysql/composition.yaml
   ```

2. Commit and push to trigger ArgoCD sync

4. Verify XRD and Composition are created:
   ```bash
   kubectl get xrd xmysqldatabases.platform.io
   kubectl get composition xmysqldatabase.platform.io
   ```

### Phase 2 Steps
1. Create test application directory:
   ```bash
   mkdir -p base-apps/test-mysql-app
   ```

2. Add test claim and secret configuration

3. Create test password in Vault:
   ```bash
   vault kv put secret/test-app/database password=<generated-password>
   ```

4. Commit and push to deploy test application

5. Verify resources are created:
   ```bash
   # Check claim
   kubectl get mysqldatabase -n test-app test-app-db
   
   # Check composite resource
   kubectl get xmysqldatabase
   
   # Check managed resources
   kubectl get users.mysql.sql.crossplane.io -n test-app-backend
   kubectl get databases.mysql.sql.crossplane.io -n test-app
   kubectl get grants.mysql.sql.crossplane.io -n test-app
   ```

## Testing Validation

### Functional Tests
1. Database creation in MySQL
2. User creation with correct password
3. Grants applied correctly
4. Connection secret created with all keys

### Commands for Validation
```bash
# Check if database exists
kubectl exec -it mysql-0 -n mysql -- mysql -u root -p -e "SHOW DATABASES LIKE 'testapp_db';"

# Check if user exists
kubectl exec -it mysql-0 -n mysql -- mysql -u root -p -e "SELECT User, Host FROM mysql.user WHERE User='testapp_user';"

# Check grants
kubectl exec -it mysql-0 -n mysql -- mysql -u root -p -e "SHOW GRANTS FOR 'testapp_user'@'%';"

# Test connection
kubectl run mysql-test --rm -it --image=mysql:8.0 --restart=Never -- \
  mysql -h mysql.mysql.svc.cluster.local -u testapp_user -p testapp_db -e "SELECT 1;"
```

## Rollback Plan

If issues arise:
1. Delete the test application
2. Keep XRD and Composition for debugging
3. Existing applications remain unaffected
4. Fix issues and redeploy

## Next Steps

After successful testing:
- Document learnings and any adjustments needed
- Plan migration strategy for existing applications
- Prepare for Backstage template integration (Phase 3)