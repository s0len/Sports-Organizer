#!/bin/bash

if [ "$DEBUG" = "true" ]; then
  set -x  # Enable debug mode
fi

# set -e

: "${PUSHOVER_NOTIFICATION:=false}"
: "${SRC_DIR:=/data/torrents/sport}"
: "${DEST_DIR:=/data/media/sport}"
: "${PROCESS_INTERVAL:=60}"

# Add signal handling for graceful shutdown
handle_exit() {
    echo "Received shutdown signal, exiting gracefully..."
    # Print summary before exiting
    print_summary
    exit 0
}

trap handle_exit SIGTERM SIGINT

# Initialize counters
processed_count=0
skipped_count=0
error_count=0

if [ "$PUSHOVER_NOTIFICATION" = true ]; then
    if [ -z "$PUSHOVER_USER_KEY" ]; then
        echo "Error: \"PUSHOVER_USER_KEY\" is missing while \"PUSHOVER_NOTIFICATION\" is \"true\"."
        exit_bool=true
    fi

    if [ -z "$PUSHOVER_API_TOKEN" ]; then
        echo "Error: \"PUSHOVER_API_TOKEN\" is missing while \"PUSHOVER_NOTIFICATION\" is \"true\"."
        exit_bool=true
    fi
fi

# Validate volume mounts
if [ ! -d "$SRC_DIR" ]; then
    echo "Error: Source directory $SRC_DIR does not exist or is not mounted properly."
    exit 1
fi

if [ ! -d "$DEST_DIR" ]; then
    echo "Error: Destination directory $DEST_DIR does not exist or is not mounted properly."
    exit 1
fi

# Check write permissions
if [ ! -w "$DEST_DIR" ]; then
    echo "Error: No write permission to destination directory $DEST_DIR."
    exit 1
fi

# Function to print summary
print_summary() {
    echo "========== SUMMARY =========="
    echo "Files processed: $processed_count"
    echo "Files skipped: $skipped_count"
    echo "Errors encountered: $error_count"
    echo "============================"
    
    # Send summary notification if enabled
    if [ "$PUSHOVER_NOTIFICATION" = true ] && [ $((processed_count + skipped_count + error_count)) -gt 0 ]; then
        summary_message="Files processed: $processed_count<br>Files skipped: $skipped_count<br>Errors encountered: $error_count"
        send_pushover_notification "$summary_message" "Sports Organizer Summary"
    fi
}

# Function to organize sports files
organize_sports() {
    local file="$1"
    local filename=$(basename "$file")

    # Debug output
    echo "Processing file: $file with direct string parsing"

    # Handle both files and directories
    if [[ -d "$file" ]]; then
        # For directories, look for matching video files inside
        find "$file" -type f -name "*.mkv" | while read video_file; do
            echo "Found video file in directory: $video_file"
            organize_sports "$video_file"
        done
        return 0
    fi

    # Determine sport type based on filename
    local sport_type=""
    local year=""
    local round=""
    local location=""
    local session=""
    local episode=""

    # Check for MotoGP/Moto2/Moto3
    if [[ $filename == [Mm]oto* ]] && [[ $filename == *.mkv ]]; then
        # Process as motorcycle racing
        process_moto_racing "$file"
        return $?
    elif [[ $filename =~ [Ff]ormula* ]] && [[ $filename == *.mkv ]]; then
        process_f1_racing "$file"
        return $?
    # Fix the UFC pattern match to handle lowercase filenames
    elif [[ $filename =~ ^[Uu][Ff][Cc]\.* ]] && [[ $filename == *.mkv ]]; then
        process_ufc "$file"
        return $?
    else
        echo "Unknown sport type in filename: $filename"
        ((error_count++))
        return 1
    fi
}

# Function to process motorcycle racing files
process_moto_racing() {
    local file="$1"
    local filename=$(basename "$file")

    # Get class (MotoGP, Moto2, Moto3)
    local sport_type=""
    if [[ $filename == MotoGP* ]]; then
        sport_type="MotoGP"
    elif [[ $filename == Moto2* ]]; then
        sport_type="Moto2"
    elif [[ $filename == Moto3* ]]; then
        sport_type="Moto3"
    else
        echo "Unknown Moto class in filename: $filename"
        ((error_count++))
        return 1
    fi

    # Get year (next part after the first dot)
    IFS='.' read -ra PARTS <<<"$filename"
    if [[ ${#PARTS[@]} -lt 4 ]]; then
        echo "Not enough parts in filename: $filename"
        ((error_count++))
        return 1
    fi

    local year="${PARTS[1]}"
    echo "Year: $year"

    # Get round number and location
    local round="${PARTS[2]#Round}" # Remove 'Round' prefix
    local location="${PARTS[3]}"
    echo "Round: $round, Location: $location"

    # Determine session and episode
    local session=""
    local episode=""

    # Check for Race
    if [[ $filename == *Race* ]]; then
        session="Race"
        if [[ $sport_type == "MotoGP" ]]; then
            episode="6" # MotoGP races are episode 6 (after Sprint)
        else
            episode="5" # Moto2/3 races are episode 5 (no Sprint)
        fi
    # Check for Sprint - only for MotoGP class
    elif [[ $filename == *Sprint* ]]; then
        if [[ $sport_type == "MotoGP" ]]; then
            session="Sprint"
            episode="5"
        else
            # For Moto2/3 classes that don't have Sprint races
            session="Unknown"
            episode="0"
            echo "Warning: Sprint session found for $sport_type which shouldn't have sprints"
        fi
    # Check for Qualifying
    elif [[ $filename == *Qualifying* ]]; then
        if [[ $filename == *Q1* ]]; then
            session="Qualifying 1"
            episode="3"
        elif [[ $filename == *Q2* ]]; then
            session="Qualifying 2"
            episode="4"
        else
            session="Qualifying"
            episode="3"
        fi
    # Check for Practice
    elif [[ $filename == *FP* ]] || [[ $filename == *Practice* ]]; then
        if [[ $filename == *"FP1"* ]] || [[ $filename == *"Practice One"* ]]; then
            session="Free Practice 1"
            episode="1"
        elif [[ $filename == *"FP2"* ]] || [[ $filename == *"Practice Two"* ]]; then
            session="Free Practice 2"
            episode="2"
        else
            session="Free Practice"
            episode="1"
        fi
    else
        session="Unknown"
        episode="0"
    fi

    echo "Session: $session, Episode: $episode"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/$sport_type $year"
    local round_dir="$season_dir/$round $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/${round}x${episode} ${sport_type} ${session}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "Moving: $file to $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "Successfully processed file!"
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        ((error_count++))
        return 1
    fi

    return 0
}

# Function to process UFC files
process_ufc() {
    local file="$1"
    local filename=$(basename "$file")

    # Skip sample files
    if [[ $filename == *sample* ]]; then
        echo "Skipping sample file: $filename"
        ((skipped_count++))
        return 0
    fi

    # Check if it's a UFC file
    if [[ ! $filename =~ ^ufc\. ]]; then
        echo "Not a UFC file: $filename"
        ((error_count++))
        return 1
    fi

    # Parse UFC number (season)
    local season=""
    if [[ $filename =~ ^ufc\.([0-9]+)\. ]]; then
        season="${BASH_REMATCH[1]}"
    else
        echo "Could not parse UFC number from filename: $filename"
        ((error_count++))
        return 1
    fi

    # Parse event name (fighters)
    local event_name=""
    if [[ $filename =~ ^ufc\.[0-9]+\.(.+?)\.(early\.prelims|prelims|ppv)\. ]]; then
        event_name="${BASH_REMATCH[1]}"
        # Replace dots with spaces for readability
        event_name="${event_name//./ }"
    else
        echo "Could not parse event name from filename: $filename"
        ((error_count++))
        return 1
    fi

    # Determine episode type and number
    local episode_type=""
    local episode_num=""
    if [[ $filename == *early.prelims* ]]; then
        episode_type="Early Prelims"
        episode_num="1"
    elif [[ $filename == *prelims* && ! $filename == *early.prelims* ]]; then
        episode_type="Prelims"
        episode_num="2"
    elif [[ $filename == *ppv* ]]; then
        episode_type="Main Card"
        episode_num="3"
    else
        echo "Unknown episode type in filename: $filename"
        ((error_count++))
        return 1
    fi

    echo "Season: $season, Event: $event_name, Episode Type: $episode_type, Episode Number: $episode_num"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/UFC"
    local event_dir="$season_dir/$season $event_name"
    mkdir -p "$event_dir"

    # Create the target filename
    local target_file="$event_dir/${season}x${episode_num} UFC $episode_type.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "Moving: $file to $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "Successfully processed file!"
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        ((error_count++))
        return 1
    fi

    return 0
}

process_f1_racing() {
    local file="$1"
    local filename=$(basename "$file")

    local sport_type=""
    if [[ $filename =~ ([Ff]ormula[[:space:]]*1|[Ff]ormula1|[Ff]1) ]]; then
        sport_type="Formula 1"
    elif [[ $filename =~ ([Ff]ormula[[:space:]]*2|[Ff]ormula2|[Ff]2) ]]; then
        sport_type="Formula 2"
    elif [[ $filename =~ ([Ff]ormula[[:space:]]*3|[Ff]ormula3|[Ff]3) ]]; then
        sport_type="Formula 3"
    else
        echo "Unknown Formula class in filename: $filename"
        ((error_count++))
        return 1
    fi

    # Get year (next part after the first dot)
    IFS='.' read -ra PARTS <<<"$filename"
    if [[ ${#PARTS[@]} -lt 4 ]]; then
        echo "Not enough parts in filename: $filename"
        ((error_count++))
        return 1
    fi

    local year="${PARTS[1]}"
    echo "Year: $year"

    # Get round number and location
    local round="${PARTS[2]#Round}" # Remove 'Round' prefix
    local location="${PARTS[3]}"
    echo "Round: $round, Location: $location"

    # Determine session and episode
    local session=""
    local episode=""

    # Check for Drivers Press Conference
    if [[ $filename == *[Dd]rivers*[Pp]ress*[Cc]onference* ]]; then
        session="Drivers Press Conference"
        if [[ $sport_type == "Formula 1" ]]; then
            episode="1"
        fi
    # Check for Weekend Warm Up
    elif [[ $filename == *[Ww]eekend*[Ww]arm* ]]; then
        session="Weekend Warm Up"
        if [[ $sport_type == "Formula 1" ]]; then
            episode="2"
        fi
    # Check for Practice
    elif [[ $filename == *FP* ]] || [[ $filename == *Practice* ]]; then
        if [[ ($filename == *"FP1"* || $filename == *"Practice One"*) && $sport_type == "Formula 1" ]]; then
            session="Free Practice 1"
            episode="3"
        elif [[ ($filename == *"FP2"* || $filename == *"Practice Two"*) && $sport_type == "Formula 1" ]]; then
            session="Free Practice 2"
            episode="4"
        elif [[ ($filename == *"FP3"* || $filename == *"Practice Three"*) && $sport_type == "Formula 1" ]]; then
            session="Free Practice 3"
            episode="5"
        else
            session="Free Practice"
            episode="1"
        fi
    # Check for Sprint
    elif [[ $filename == *Sprint* ]]; then
        if [[ $filename == *Qualifying* && $sport_type == "Formula 1" ]]; then
            session="Sprint Qualifying"
            episode="4"
        elif [[ $filename == *Sprint* && $sport_type == "Formula 1" ]]; then
            session="Sprint"
            episode="5"
        else
            # For Formula classes that don't have Sprint races
            session="Unknown"
            episode="0"
            echo "Warning: Sprint session found for $sport_type which shouldn't have sprints"
        fi
    # Check for Qualifying
    elif [[ $filename == *Qualifying* ]]; then
        if [[ $filename == *Pre*Qualifying* ]]; then
            session="Pre Qualifying Show"
            episode="6"
        elif [[ $filename == *Post*Qualifying* ]]; then
            session="Post Qualifying Show"
            episode="8"
        else
            session="Qualifying"
            if [[ $sport_type == "Formula 1" ]]; then
                episode="7"
            else
                episode="2"
            fi
        fi
    # Check for Race
    elif [[ $filename == *Race* ]]; then
        if [[ $filename == *Pre*Race* ]]; then
            session="Pre Race Show"
            episode="9"
        elif [[ $filename == *Post*Race* ]]; then
            session="Post Race Show"
            episode="11"
        elif [[ $filename == *Sprint*Race* ]]; then
            session="Sprint Race"
            episode="3"
        elif [[ $filename == *Feature*Race* ]]; then
            session="Feature Race"
            episode="4"
        else
            session="Race"
            if [[ $sport_type == "Formula 1" ]]; then
                episode="10"
            else
                episode="3"
            fi
        fi
    else
        session="Unknown"
        episode="0"
    fi

    echo "Session: $session, Episode: $episode"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/$sport_type $year"
    local round_dir="$season_dir/$round $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/${round}x${episode} ${sport_type} ${session}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "Moving: $file to $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "Successfully processed file!"
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        ((error_count++))
        return 1
    fi

    return 0
}

send_pushover_notification() {
    if [ "$PUSHOVER_NOTIFICATION" = true ]; then
        local message="$1"
        local title="$2"
        response=$(curl -s -w "%{http_code}" --form-string "token=${PUSHOVER_API_TOKEN}" \
            --form-string "user=${PUSHOVER_USER_KEY}" \
            --form-string "message=${message}" \
            --form-string "title=${title}" \
            --form-string "html=1" \
            https://api.pushover.net/1/messages.json)

        http_code="${response: -3}"
        if [ "$http_code" -ne 200 ]; then
            echo "Warning: Failed to send Pushover notification. HTTP status code: $http_code"
        fi
    fi
}

send_pushover_error_notification() {
    if [ "$PUSHOVER_NOTIFICATION" = true ]; then
        local message="$1"
        local title="$2"
        response=$(curl -s -w "%{http_code}" --form-string "token=${PUSHOVER_API_TOKEN}" \
            --form-string "user=${PUSHOVER_USER_KEY}" \
            --form-string "message=${message}" \
            --form-string "title=${title}" \
            --form-string "priority=1" \
            --form-string "html=1" \
            https://api.pushover.net/1/messages.json)

        http_code="${response: -3}"
        if [ "$http_code" -ne 200 ]; then
            echo "Warning: Failed to send Pushover error notification. HTTP status code: $http_code"
        fi
    fi
}

# Process existing files (direct approach)
echo "Starting with SRC_DIR=$SRC_DIR and DEST_DIR=$DEST_DIR"
echo "Checking for files in $SRC_DIR"
if [ -d "$SRC_DIR" ]; then
  ls -la "$SRC_DIR" || echo "Cannot list contents of $SRC_DIR"
else
  echo "ERROR: Source directory $SRC_DIR does not exist or is not accessible"
  echo "Waiting for directory to become available..."
  # Maybe add a loop to wait for the directory
fi

echo "Looking for sports files..."
while IFS= read -r file; do
    echo "Found MKV file: $file"
    organize_sports "$file"
done < <(find "$SRC_DIR" -name "*.mkv")

# Print summary after initial processing
echo "Initial file processing completed."
print_summary

# Reset counters for monitoring phase
processed_count=0
skipped_count=0
error_count=0

echo "Starting file monitoring..."
echo "Starting continuous monitoring (interval: ${PROCESS_INTERVAL}s)..."

# Monitor for new files and directories
while true; do
    echo "$(date): Checking for new files..."
    
    # Debug output for monitoring loop
    echo "DEBUG: Running find command: find \"$SRC_DIR\" -name \"*.mkv\" -mmin -$((PROCESS_INTERVAL/60+1))\""
    
    # Check for new files periodically
    find_output=$(find "$SRC_DIR" -name "*.mkv" -mmin -$((PROCESS_INTERVAL/60+1)) 2>&1)
    find_status=$?
    
    if [ $find_status -ne 0 ]; then
        echo "ERROR: Find command failed with status $find_status: $find_output"
    else
        echo "DEBUG: Find command completed successfully"
    fi
    
    echo "$find_output" | while read file; do
        if [ -n "$file" ]; then
            echo "Found new MKV file: $file"
            organize_sports "$file"
            
            # Send notification for successful processing
            if [ "$PUSHOVER_NOTIFICATION" = true ]; then
                send_pushover_notification "Processed: $(basename "$file")" "Sports Organizer"
            fi
        fi
    done
    
    # Print periodic summary if files were processed
    if [ $((processed_count + skipped_count + error_count)) -gt 0 ]; then
        echo "Summary for this check cycle:"
        print_summary
        
        # Reset counters after printing summary
        processed_count=0
        skipped_count=0
        error_count=0
    else
        echo "DEBUG: No files processed in this cycle"
    fi
    
    echo "DEBUG: Sleeping for $PROCESS_INTERVAL seconds..."
    sleep $PROCESS_INTERVAL
    echo "DEBUG: Woke up from sleep"
done
