#!/bin/bash
# Send deployment notification
# This script sends notifications to configured channels

set -e

# Configuration
STATUS=$1
MESSAGE=$2
COMMIT_SHA=${3:-$(git rev-parse HEAD)}
AUTHOR=${4:-$(git log -1 --pretty=format:'%an')}

if [ -z "$STATUS" ] || [ -z "$MESSAGE" ]; then
    echo "Usage: ./notify-deployment.sh <status> <message> [commit-sha] [author]"
    echo "Example: ./notify-deployment.sh success 'Deployment completed' abc123 'John Doe'"
    exit 1
fi

# Determine color based on status
case "$STATUS" in
    success)
        COLOR="#36a64f"
        EMOJI=":white_check_mark:"
        ;;
    failure)
        COLOR="#ff0000"
        EMOJI=":x:"
        ;;
    warning)
        COLOR="#ffaa00"
        EMOJI=":warning:"
        ;;
    *)
        COLOR="#808080"
        EMOJI=":information_source:"
        ;;
esac

# Get deployment info
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
REGION="${AWS_REGION:-us-east-1}"

echo "=========================================="
echo "Sending Deployment Notification"
echo "=========================================="
echo "Status: $STATUS"
echo "Message: $MESSAGE"
echo "Commit: $COMMIT_SHA"
echo "Author: $AUTHOR"
echo "Timestamp: $TIMESTAMP"
echo ""

# Slack notification (if webhook URL is configured)
if [ -n "$SLACK_WEBHOOK_URL" ]; then
    echo "Sending Slack notification..."
    
    SLACK_PAYLOAD=$(cat <<EOF
{
    "attachments": [
        {
            "color": "$COLOR",
            "title": "$EMOJI BDO Market Insights Deployment",
            "fields": [
                {
                    "title": "Status",
                    "value": "$STATUS",
                    "short": true
                },
                {
                    "title": "Region",
                    "value": "$REGION",
                    "short": true
                },
                {
                    "title": "Message",
                    "value": "$MESSAGE",
                    "short": false
                },
                {
                    "title": "Commit",
                    "value": "$COMMIT_SHA",
                    "short": true
                },
                {
                    "title": "Author",
                    "value": "$AUTHOR",
                    "short": true
                },
                {
                    "title": "Timestamp",
                    "value": "$TIMESTAMP",
                    "short": false
                }
            ]
        }
    ]
}
EOF
)
    
    curl -X POST \
        -H 'Content-type: application/json' \
        --data "$SLACK_PAYLOAD" \
        "$SLACK_WEBHOOK_URL"
    
    echo "Slack notification sent!"
fi

# Email notification (if SES is configured)
if [ -n "$NOTIFICATION_EMAIL" ]; then
    echo "Sending email notification..."
    
    EMAIL_SUBJECT="[BDO Market Insights] Deployment $STATUS"
    EMAIL_BODY="Deployment Status: $STATUS\n\nMessage: $MESSAGE\n\nCommit: $COMMIT_SHA\nAuthor: $AUTHOR\nRegion: $REGION\nTimestamp: $TIMESTAMP"
    
    aws ses send-email \
        --from "noreply@example.com" \
        --to "$NOTIFICATION_EMAIL" \
        --subject "$EMAIL_SUBJECT" \
        --text "$EMAIL_BODY" \
        --region "$REGION" 2>/dev/null || echo "Email notification failed (SES may not be configured)"
fi

# SNS notification (if topic ARN is configured)
if [ -n "$SNS_TOPIC_ARN" ]; then
    echo "Sending SNS notification..."
    
    SNS_MESSAGE="BDO Market Insights Deployment\n\nStatus: $STATUS\nMessage: $MESSAGE\nCommit: $COMMIT_SHA\nAuthor: $AUTHOR\nRegion: $REGION\nTimestamp: $TIMESTAMP"
    
    aws sns publish \
        --topic-arn "$SNS_TOPIC_ARN" \
        --subject "Deployment $STATUS" \
        --message "$SNS_MESSAGE" \
        --region "$REGION" 2>/dev/null || echo "SNS notification failed (topic may not exist)"
fi

echo ""
echo "=========================================="
echo "Notification sent successfully!"
echo "=========================================="

exit 0
