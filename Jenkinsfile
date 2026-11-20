pipeline {
  agent any
  options {
    timestamps()
    disableConcurrentBuilds()
    timeout(time: 90, unit: 'MINUTES')
    skipDefaultCheckout(true)
  }
  parameters {
    string(name: 'DB_NAME', defaultValue: 'jj_nsrl', description: 'MySQL database name (e.g., your initials_nsrl).')
    string(name: 'STORAGE_GB', defaultValue: '20', description: 'RDS allocated storage in GiB (20 to 100). Existing storage cannot shrink.')
    choice(name: 'DB_INSTANCE_CLASS', choices: ['db.t3.micro', 'db.t3.small', 'db.t3.medium'], description: 'RDS compute class.')
    booleanParam(name: 'MULTI_AZ', defaultValue: true, description: 'Enable RDS MySQL Multi-AZ synchronous Standby DB for automatic failover.')
    string(name: 'VPC_ID', defaultValue: '', description: 'VPC containing the Jenkins host and RDS DB subnet group.')
    string(name: 'DB_SUBNET_GROUP', defaultValue: '', description: 'Existing RDS DB subnet group NAME (not a subnet-... ID).')
    string(name: 'JENKINS_SECURITY_GROUP_ID', defaultValue: '', description: 'Security group ID attached to this Jenkins host.')
    string(name: 'STACK_NAME', defaultValue: 'nsrl-mysql-db', description: 'Unique CloudFormation stack name for this RDS MySQL database.')
    choice(name: 'AWS_REGION', choices: ['us-east-1', 'us-west-2'], description: 'AWS Academy Learner Lab region.')
    booleanParam(name: 'RUN_DEPLOYMENT', defaultValue: false, description: 'Check to deploy/update RDS MySQL and load the NSRL dataset from S3.')
    booleanParam(name: 'RUN_FAILOVER_DR_TEST', defaultValue: false, description: 'Check to run live Multi-AZ standby failover and snapshot backup/restore DR verification.')
  }
  triggers { pollSCM('H/2 * * * *') }
  stages {
    stage('Checkout GitHub') {
      steps {
        script {
          def revision = checkout scm
          env.GIT_COMMIT = revision.GIT_COMMIT
        }
        sh 'mkdir -p artifacts'
      }
    }
    stage('CI checks') {
      steps {
        sh '''
          python3 -m unittest discover -s tests -v
          python3 -m compileall -q scripts app
          aws cloudformation validate-template --template-body file://infra/mysql-rds.yaml >/dev/null
        '''
      }
    }
    stage('Validate parameters and network') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py preflight' }
    }
    stage('Deploy CloudFormation RDS MySQL') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py deploy' }
    }
    stage('Publish NSRL SQL and CSVs to S3') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py publish' }
    }
    stage('Extract and verify S3 release') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py download' }
    }
    stage('Load MySQL NSRL over TLS') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py load' }
    }
    stage('Verify MySQL NSRL & SSL') {
      when { expression { params.RUN_DEPLOYMENT } }
      steps { sh 'python3 scripts/workshop.py verify' }
    }
    stage('Standby Failover & Snapshot DR') {
      when { expression { params.RUN_DEPLOYMENT && params.RUN_FAILOVER_DR_TEST } }
      steps { sh 'python3 scripts/workshop.py failover_dr' }
    }
  }
  post {
    always {
      archiveArtifacts artifacts: 'artifacts/*.json', allowEmptyArchive: true
      deleteDir()
    }
  }
}
