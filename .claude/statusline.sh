#!/bin/bash
# Multi-line Claude Code statusline
# Line 1: Model, directory, context usage (original)
# Line 2: Rate limit windows (5h/7d) and extra spending (new)

input=$(cat)

# Line 1: Model, directory, context
MODEL=$(echo "$input" | jq -r '.model.display_name')
DIR=$(echo "$input" | jq -r '.workspace.current_dir')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)

echo "[$MODEL] 📁 ${DIR##*/} | ${PCT}% context"

# Line 2: Rate limits and spending
FIVE_H_PCT=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
SEVEN_D_PCT=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')

# Color codes
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
CYAN='\033[36m'
RESET='\033[0m'

# Color rate limits: red if near 100%, yellow if moderate, green if low
color_rate_limit() {
    local pct="$1"
    if (( $(echo "$pct >= 90" | bc -l 2>/dev/null || echo 0) )); then
        echo -n "$RED"
    elif (( $(echo "$pct >= 70" | bc -l 2>/dev/null || echo 0) )); then
        echo -n "$YELLOW"
    else
        echo -n "$GREEN"
    fi
}

# Build rate limits display
RATE_LIMITS=""
if [ -n "$FIVE_H_PCT" ]; then
    FIVE_H_INT=$(printf '%.0f' "$FIVE_H_PCT")
    RATE_LIMITS="${RATE_LIMITS}$(color_rate_limit "$FIVE_H_INT")5h: ${FIVE_H_INT}%${RESET}"
fi

if [ -n "$SEVEN_D_PCT" ]; then
    SEVEN_D_INT=$(printf '%.0f' "$SEVEN_D_PCT")
    [ -n "$RATE_LIMITS" ] && RATE_LIMITS="${RATE_LIMITS} "
    RATE_LIMITS="${RATE_LIMITS}$(color_rate_limit "$SEVEN_D_INT")7d: ${SEVEN_D_INT}%${RESET}"
fi

# Format cost display
COST_FMT=$(printf '$%.2f' "$COST")

# Build second line
STATUS_LINE2=""
if [ -n "$RATE_LIMITS" ]; then
    STATUS_LINE2="${RATE_LIMITS} | "
fi
STATUS_LINE2="${STATUS_LINE2}${CYAN}${COST_FMT}${RESET}"

echo -e "$STATUS_LINE2"