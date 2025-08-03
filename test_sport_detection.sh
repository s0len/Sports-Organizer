#!/bin/bash

# Test sport type detection logic directly
function test_sport_detection() {
    local filename="$1"
    local sport_type=""
    
    echo "Testing filename: $filename"
    
    # Check for MWR space-separated format first (e.g. "Formula1 2025 Round14...")
    if [[ $filename =~ ^[Ff]ormula1\ [0-9] ]]; then
        sport_type="Formula1"
        echo "✓ Matched MWR space format: sport_type=$sport_type"
    elif [[ $filename =~ ^[Ff]ormula2\ [0-9] ]]; then
        sport_type="Formula2"
        echo "✓ Matched MWR space format: sport_type=$sport_type"
    elif [[ $filename =~ ^[Ff]ormula3\ [0-9] ]]; then
        sport_type="Formula3"
        echo "✓ Matched MWR space format: sport_type=$sport_type"
    elif [[ $filename =~ ^[Ff]ormula[Ee]\ [0-9] ]]; then
        sport_type="Formula E"
        echo "✓ Matched MWR space format: sport_type=$sport_type"
    # Standard dot/dash separated format (e.g. "Formula1.2024.Round01...")
    elif [[ $filename =~ [Ff]ormula1[\.\-] ]]; then
        sport_type="Formula1"
        echo "✓ Matched standard dot/dash format: sport_type=$sport_type"
    elif [[ $filename =~ [Ff]ormula2[\.\-] ]]; then
        sport_type="Formula2"
        echo "✓ Matched standard dot/dash format: sport_type=$sport_type"
    elif [[ $filename =~ [Ff]ormula3[\.\-] ]]; then
        sport_type="Formula3"
        echo "✓ Matched standard dot/dash format: sport_type=$sport_type"
    elif [[ $filename =~ [Ff]ormula[Ee][\.\-] ]]; then
        sport_type="Formula E"
        echo "✓ Matched standard dot/dash format: sport_type=$sport_type"
    else
        echo "✗ No Formula pattern matched - would show 'Unknown Formula class'"
        return 1
    fi
    
    return 0
}

echo "=== Testing Standard Dot-Separated Format ==="
test_sport_detection "Formula1.2024.Round01.Bahrain.Practice.Session.1.mkv"

echo ""
echo "=== Testing MWR Space-Separated Format ==="
test_sport_detection "Formula1 2025 Round14 Hungary Qualifying.mkv"

echo ""
echo "=== Testing Formula2 Standard Format ==="
test_sport_detection "Formula2.2024.Round05.Monaco.Race.mkv"

echo ""
echo "=== Testing Formula2 MWR Format ==="
test_sport_detection "Formula2 2025 Round08 Silverstone Sprint.mkv"