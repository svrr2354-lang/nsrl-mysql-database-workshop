#!/bin/bash
# Run on the dedicated EC2 host through AWS Systems Manager Run Command.
set -euo pipefail
if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo 'Usage: setup_jenkins.sh <GIT_REPO_URL> <REGION> [JOB_NAME]'
  exit 2
fi
REPO="$1"
REGION="$2"
JOB_NAME="${3:-nsrl-mysql-database-delivery}"

cd "$(dirname "$0")/.."
systemctl is-active --quiet docker

mkdir -p /opt/nsrl-mysql-workshop/auth /opt/nsrl-mysql-workshop/jenkins-home/.ssh
chmod 700 /opt/nsrl-mysql-workshop/auth

if [[ ! -f /opt/nsrl-mysql-workshop/auth/deploy-key ]]; then
  ssh-keygen -q -t ed25519 -N '' -f /opt/nsrl-mysql-workshop/auth/deploy-key -C nsrl-mysql-jenkins
fi

# Import GitHub's published SSH host keys for verified SSH cloning.
curl -fsSL https://api.github.com/meta | python3 -c 'import sys,json; print("\n".join("github.com "+k for k in json.load(sys.stdin)["ssh_keys"]))' > /opt/nsrl-mysql-workshop/jenkins-home/.ssh/known_hosts

aws secretsmanager get-secret-value \
  --region "$REGION" \
  --secret-id "$(cat /opt/nsrl-mysql-workshop/login-secret-arn)" \
  --query SecretString --output text \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["password"])' \
  > /opt/nsrl-mysql-workshop/auth/admin-password

chown -R 1000:1000 /opt/nsrl-mysql-workshop/auth /opt/nsrl-mysql-workshop/jenkins-home
chmod 600 /opt/nsrl-mysql-workshop/auth/admin-password /opt/nsrl-mysql-workshop/auth/deploy-key
chmod 755 /opt/nsrl-mysql-workshop/auth
chmod 700 /opt/nsrl-mysql-workshop/jenkins-home/.ssh
chmod 600 /opt/nsrl-mysql-workshop/jenkins-home/.ssh/known_hosts

docker build -f infra/Jenkins.Dockerfile -t nsrl-mysql-jenkins:local .

EXTRA_MOUNTS=()
if [[ -d /opt/nsrl-mysql-workshop/local-repo.git ]]; then
  chown -R 1000:1000 /opt/nsrl-mysql-workshop/local-repo.git
  EXTRA_MOUNTS=(-v /opt/nsrl-mysql-workshop/local-repo.git:/opt/nsrl-mysql-workshop/local-repo.git:ro)
fi

if docker container inspect nsrl-mysql-jenkins >/dev/null 2>&1; then
  echo 'Jenkins container already exists. Setup did not replace it.'
else
  docker run -d \
    --name nsrl-mysql-jenkins \
    --restart unless-stopped \
    -p 127.0.0.1:8080:8080 \
    -e AWS_REGION="$REGION" \
    -e AWS_DEFAULT_REGION="$REGION" \
    -e WORKSHOP_REPO="$REPO" \
    -e JENKINS_JOB_NAME="$JOB_NAME" \
    -v /opt/nsrl-mysql-workshop/auth:/run/workshop:ro \
    -v /opt/nsrl-mysql-workshop/jenkins-home:/var/jenkins_home \
    "${EXTRA_MOUNTS[@]}" \
    nsrl-mysql-jenkins:local
fi

echo 'Add the following PUBLIC key to GitHub Settings > Deploy keys (leave Allow write access unchecked):'
cat /opt/nsrl-mysql-workshop/auth/deploy-key.pub
echo 'Jenkins is bound privately to 127.0.0.1:8080. Connect using an SSM port-forwarding session.'
