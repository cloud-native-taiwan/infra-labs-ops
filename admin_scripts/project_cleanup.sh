#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 --project <project_name>"
    echo "Please make sure admin credential is in environment variable"
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --project)
            PROJECT_NAME="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

# Check if project name is provided
if [ -z "$PROJECT_NAME" ]; then
    usage
fi

# Get project ID from project name
PROJECT_ID=$(openstack project show -f value -c id "$PROJECT_NAME")

# Check if project ID was successfully retrieved
if [ -z "$PROJECT_ID" ]; then
    echo "Error: Could not get project ID for project $PROJECT_NAME"
    exit 1
fi

# Add admin role to user genekuo for the specified project
echo "Adding admin role to user admin for project $PROJECT_NAME..."
openstack role add --user admin --project "$PROJECT_NAME" admin

# Function to check if dry-run is empty
check_dryrun() {
    local output
    output=$(OS_PROJECT_ID="$PROJECT_ID" OS_PROJECT_NAME="$PROJECT_NAME" openstack project cleanup --project "$PROJECT_NAME" --dry-run)
    [[ -z "$output" ]]
}

# Run cleanup until no resources are left
while true; do
    echo "Running project cleanup with dry-run..."
    OS_PROJECT_ID="$PROJECT_ID" OS_PROJECT_NAME="$PROJECT_NAME" openstack project cleanup --project "$PROJECT_NAME" --dry-run

    if check_dryrun; then
        echo "No more resources to clean up."
        break
    fi

    echo "Executing cleanup..."
    OS_PROJECT_ID="$PROJECT_ID" OS_PROJECT_NAME="$PROJECT_NAME" openstack project cleanup --project "$PROJECT_NAME"

    echo "Waiting for 10 seconds before next check..."
    sleep 10
done

# Remove admin role from user genekuo for the specified project
echo "Removing admin role from user genekuo for project $PROJECT_NAME..."
openstack role remove --user admin --project "$PROJECT_NAME" admin

openstack project delete "$PROJECT_NAME"
openstack user delete "$PROJECT_NAME"

echo "Script completed."


