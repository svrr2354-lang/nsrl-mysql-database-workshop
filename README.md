# NSRL MySQL 8.4 Database Query System Workshop (CSCI 6441)

Replaces the earlier PostgreSQL NSRL S3 project with Amazon RDS MySQL 8.4 using GitHub, GitHub Actions CI, Jenkins CI/CD, CloudFormation, private versioned Amazon S3, AWS Secrets Manager, enforced SSL/TLS (`require_secure_transport=ON`), Multi-AZ Standby DB failover, and manual Snapshot Backup/Restore Disaster Recovery.

Includes a browser-based NSRL MySQL Query GUI (`app/nsrl_gui.py`) as well as SQL queries (`sql/queries.sql`) for MySQL Workbench and DBeaver over private AWS Systems Manager (SSM) tunnels.

Select `RUN_DEPLOYMENT` in Jenkins to deploy and load the database. Each load drops and recreates the workshop tables, so use a dedicated database and back up any data you need to keep. The failover and restore test additionally requires `RUN_FAILOVER_DR_TEST`. Retained S3 buckets and snapshots can continue to incur charges after stack deletion.

VS Code tasks use local port 13306 for the SSM tunnel and GUI. Task 3 requires `.lab/global-bundle.pem`, a database secret ARN, and a database name. Through a localhost tunnel the GUI validates the CA chain, but does not verify the RDS hostname by default.

Deployment and data loading run only when `RUN_DEPLOYMENT` is selected in Jenkins. The loader drops and recreates the workshop tables on every load, so use a dedicated database and back up any data you need to keep. Multi-AZ failover and snapshot restore run only when `RUN_FAILOVER_DR_TEST` is also selected. Retained S3 buckets and RDS snapshots may continue to incur charges after stack deletion.

VS Code tasks use the same local port (13306) for the SSM tunnel and application. Task 3 requires `.lab/global-bundle.pem`, a database secret ARN, and the database name. With a tunnel to localhost, the GUI validates the CA chain but does not verify the RDS hostname by default.
