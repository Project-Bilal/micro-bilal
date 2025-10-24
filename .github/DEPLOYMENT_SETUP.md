# GitHub Actions Deployment Setup

## Step 1: Generate SSH Key on Server

SSH into your server and run:

```bash
# Generate a new SSH key specifically for GitHub Actions
ssh-keygen -t ed25519 -f ~/.ssh/github_actions_deploy -N ""

# Add the public key to authorized_keys
cat ~/.ssh/github_actions_deploy.pub >> ~/.ssh/authorized_keys

# Display the PRIVATE key (you'll need this for GitHub)
cat ~/.ssh/github_actions_deploy
```

**Copy the entire private key output** (from `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----`)

## Step 2: Add Secrets to GitHub

Go to your GitHub repo: https://github.com/Project-Bilal/micro-bilal/settings/secrets/actions

Click **"New repository secret"** and add these two secrets:

### Secret 1: `SERVER_SSH_KEY`

- Name: `SERVER_SSH_KEY`
- Value: Paste the **entire private key** from Step 1

### Secret 2: `SERVER_USERNAME`

- Name: `SERVER_USERNAME`
- Value: Your server username (probably `root` or your user)

## Step 3: Verify Server Path

Make sure the target directory exists on your server:

```bash
# On your server
sudo mkdir -p /var/www/html/app
sudo chown www-data:www-data /var/www/html/app
sudo chmod 755 /var/www/html/app
```

## Step 4: Test the Deployment

1. Commit and push the workflow file to GitHub
2. Make a small change to any file in `source/`
3. Push the change
4. Go to GitHub Actions tab to watch the deployment
5. Verify files appear on your server at `http://34.53.103.114/app/`

## Troubleshooting

If deployment fails:

- Check GitHub Actions logs for errors
- Verify SSH key is correct in secrets
- Ensure server user has write permissions to `/var/www/html/app/`
- Test SSH connection manually: `ssh -i ~/.ssh/github_actions_deploy user@34.53.103.114`
