#!/bin/bash

# Source the main script functions
source entrypoint.sh

# Test both file formats
echo "=== Testing Standard Format ==="
test_file1="Formula1.2024.Round01.Bahrain.Practice.Session.1.mkv"
echo "Testing: $test_file1"

# Call the F1 processing function directly (this would be called from organize_sports)
if [[ $test_file1 =~ [Ff]ormula* ]]; then
    echo "Detected as Formula file, calling process_f1_racing..."
    process_f1_racing "$test_file1"
else
    echo "Not detected as Formula file"
fi

echo ""
echo "=== Testing MWR Space Format ==="
test_file2="Formula1 2025 Round14 Hungary Qualifying.mkv"
echo "Testing: $test_file2"

# Call the F1 processing function directly
if [[ $test_file2 =~ [Ff]ormula* ]]; then
    echo "Detected as Formula file, calling process_f1_racing..."
    process_f1_racing "$test_file2"
else
    echo "Not detected as Formula file"
fi