#!/bin/bash

# Configuration
CHECK_INTERVAL=60
IDENTIFIER="gpu-temp-monitor"

# Logging function with severity levels
log() {
    local level=$1
    local message=$2
    logger -t "$IDENTIFIER" --priority "user.${level,,}" "$message"
}

# Get maximum GPU temperature
get_max_temp() {
    local temps=$(racadm getsensorinfo | grep PrimaryGPUTemp)
    if [ -z "$temps" ]; then
        log error "Failed to get temperature readings from racadm"
        return 1
    fi

    log debug "Raw temperature readings:"
    while IFS= read -r line; do
        log debug "$line"
    done <<< "$temps"

    echo "$temps" | awk '{print $2}' | tr -d 'C' | sort -nr | head -n1
}

# Set minimum fan speed
set_min_fan_speed() {
    local speed=$1
    local speed_desc

    case $speed in
        255) speed_desc="auto";;
        *) speed_desc="${speed}%";;
    esac

    log info "Attempting to set minimum fan speed to $speed_desc"

    if ! racadm set system.thermalsettings.MinimumFanSpeed "$speed"; then
        log error "Failed to set minimum fan speed to $speed"
        return 1
    fi

    log info "Successfully set minimum fan speed to $speed_desc"
    return 0
}

# State change logging
log_state_change() {
    local old_state=$1
    local new_state=$2
    local temp=$3
    local fan_speed=$4

    log info "=== State Change Detected ==="
    log info "Temperature: ${temp}°C"
    log info "Previous state: ${old_state:-"initial"}"
    log info "New state: $new_state"
    log info "New fan speed: ${fan_speed}"
    log info "======================="
}

# Startup banner
log info "=== GPU Temperature Monitor Starting ==="
log info "Check interval: ${CHECK_INTERVAL} seconds"
log info "================================"

# Main loop
last_state=""
consecutive_errors=0
max_errors=3

while true; do
    # Get current maximum temperature
    max_temp=$(get_max_temp)

    if [ -z "$max_temp" ] || ! [[ "$max_temp" =~ ^[0-9]+$ ]]; then
        consecutive_errors=$((consecutive_errors + 1))
        log error "Failed to get valid temperature reading (attempt $consecutive_errors of $max_errors)"

        if [ $consecutive_errors -ge $max_errors ]; then
            log error "Maximum consecutive errors reached. Restarting service..."
            exit 1
        fi

        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Reset error counter on successful reading
    consecutive_errors=0

    # Temperature control logic with state tracking
    new_state=""
    if [ "$max_temp" -ge 84 ]; then
        if [ "$last_state" != "high" ]; then
            new_state="high"
            log_state_change "$last_state" "$new_state" "$max_temp" "70%"
            set_min_fan_speed 78
            last_state="high"
        fi
    elif [ "$max_temp" -ge 80 ]; then
        if [ "$last_state" != "medium" ]; then
            new_state="medium"
            log_state_change "$last_state" "$new_state" "$max_temp" "60%"
            set_min_fan_speed 60
            last_state="medium"
        fi
    elif [ "$max_temp" -lt 70 ]; then
        if [ "$last_state" != "normal" ]; then
            new_state="normal"
            log_state_change "$last_state" "$new_state" "$max_temp" "auto"
            set_min_fan_speed 255
            last_state="normal"
        fi
    fi

    # Regular temperature logging (only if no state change)
    if [ -z "$new_state" ]; then
        log info "Temperature: ${max_temp}°C (State: $last_state)"
    fi

    sleep "$CHECK_INTERVAL"
done
