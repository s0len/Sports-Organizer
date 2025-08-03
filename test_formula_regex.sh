#!/bin/bash
filename="Formula1.2024.Round01.Bahrain.Practice.Session.1.mkv"
echo "Testing standard dot-separated filename: $filename"

# Test new MWR space format detection first
if [[ $filename =~ ^[Ff]ormula1\ [0-9] ]]; then
    echo "✗ Incorrectly matched MWR space format"
    sport_type="Formula1"
elif [[ $filename =~ [Ff]ormula1[\.\-] ]]; then
    echo "✓ Correctly matched standard dot format"
    sport_type="Formula1"
else
    echo "✗ Failed to match any Formula1 pattern"
    sport_type=""
fi

# Test MWR space format
filename2="Formula1 2025 Round14 Hungary Qualifying.mkv"
echo ""
echo "Testing MWR space-separated filename: $filename2"

# Test new MWR space format detection first
if [[ $filename2 =~ ^[Ff]ormula1\ [0-9] ]]; then
    echo "✓ Correctly matched MWR space format"
    sport_type="Formula1"
elif [[ $filename2 =~ [Ff]ormula1[\.\-] ]]; then
    echo "✗ Incorrectly matched standard dot format"
    sport_type="Formula1"
else
    echo "✗ Failed to match any Formula1 pattern"
    sport_type=""
fi

echo ""
echo "Testing sport_type detection logic:"
echo "Standard file sport_type would be: Formula1"
echo "MWR file sport_type would be: Formula1"