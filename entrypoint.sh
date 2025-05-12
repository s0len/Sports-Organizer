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
        summary_html="<b>📊 Sports Organizer Summary</b><br><br>"
        summary_html+="<b>✅ Processed:</b> $processed_count files<br>"
        summary_html+="<b>⏭️ Skipped:</b> $skipped_count files<br>"
        
        if [ $error_count -gt 0 ]; then
            summary_html+="<b>❌ Errors:</b> $error_count files"
        else
            summary_html+="<b>✓ No errors encountered</b>"
        fi
        
        send_pushover_notification "$summary_html" "Sports Organizer Summary"
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
        echo "Directory detected, searching for video files inside"
        find "$file" -type f \( -name "*.mkv" -o -name "*.mp4" \) | while read video_file; do
            echo "Found video file in directory: $video_file"
            organize_sports "$video_file"
        done
        return 0
    fi

    # Skip sample files early
    if [[ $filename == *sample* ]]; then
        echo "Skipping sample file: $filename"
        ((skipped_count++))
        return 0
    fi

    # Determine sport type based on filename
    local sport_type=""
    local year=""
    local round=""
    local location=""
    local session=""
    local episode=""

    # Check for Isle of Man TT
    if [[ $filename =~ ^Isle\.Of\.Man\.TT\. ]]; then
        # Process as Isle of Man TT racing
        process_isle_of_man_tt "$file"
        return $?
    # Check for MotoGP/Moto2/Moto3
    elif [[ $filename == [Mm]oto* ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        # Process as motorcycle racing
        process_moto_racing "$file"
        return $?
    # Check for IndyCar
    elif [[ $filename == [Ii]ndy[Cc]ar* ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        # Process as IndyCar racing
        process_indycar_racing "$file"
        return $?
    # Check for World Superbike Championship and related series
    elif [[ $filename =~ ^(WSBK|WorldSBK|WSSP300|WSSP)[\.\-] ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        process_world_superbike "$file"
        return $?
    # Check for Formula racing
    elif [[ $filename =~ [Ff]ormula* ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        process_f1_racing "$file"
        return $?
    # Fix the UFC pattern match - use case-insensitive check
    elif [[ $filename =~ ^[uU][fF][cC][\.\-] ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        echo "Detected UFC file, sending to process_ufc"
        process_ufc "$file"
        return $?
    else
        echo "Unknown sport type in filename: $filename"
        ((error_count++))
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "Unknown sport type in filename: $filename" "Sports Organizer Error"
        fi
        return 1
    fi
}

# Function to process IndyCar racing files
process_indycar_racing() {
    local file="$1"
    local filename=$(basename "$file")

    # Get class (IndyCar, Indy Lights, etc.)
    local sport_type=""
    if [[ $filename =~ [Ii]ndy[Cc]ar(\.[Ss]eries)?[\.\-] ]]; then
        sport_type="NTT IndyCar Series"
    else
        echo "Unknown IndyCar class in filename: $filename"
        ((error_count++))
        return 1
    fi

    # Extract year from the first four digits found in the filename
    if [[ $filename =~ ([0-9]{4}) ]]; then
        local year="${BASH_REMATCH[1]}"
    else
        echo "Could not find year (4 digits) in filename: $filename"
        ((error_count++))
        return 1
    fi

    echo "Year: $year"

    # Get round number and location using regex
    local round=""
    local location=""
    if [[ $filename =~ [Rr]ound([0-9]{2})\.(.+?)\.(FP|Qualifying|Race|Practice|Sprint) ]]; then
        round="${BASH_REMATCH[1]}"
        location="${BASH_REMATCH[2]//./ }" # Replace dots with spaces
    else
        echo "Could not extract round and location from filename: $filename"
        ((error_count++))
        return 1
    fi
    echo "Round: $round, Location: $location"

    # Special handling for different IndyCar rounds
    local session=""
    local episode=""
    local round_num=$(echo "$round" | sed 's/^0*//')  # Remove leading zeros
    
    # Define episode mappings for special rounds
    # Format: round_num:total_episodes:special_mapping (where special_mapping is a comma-separated list of session=episode_num pairs)
    local special_rounds="6:10:Practice 1=1,Practice 2=2,Practice 3=3,Practice 4=4,Practice 5=5,Practice 6=6,Qualifying Day 1=7,Qualifying Day 2=8,Carb Day=9,Race=10
8:3:Practice=1,Qualifying=2,Race=3
11:3:Practice=1,Qualifying=2,Race=3
12:1:Race=1
16:3:Practice=1,Qualifying=2,Race=3
17:3:Practice=1,Qualifying=2,Race=3"

    # Default episode mapping (for most rounds with 4 episodes)
    local default_mapping="Free Practice 1=1,Free Practice 2=2,Qualifying=3,Race=4"
    
    # Check if we have special handling for this round
    local episode_mapping=""
    local total_episodes=4  # Default value
    
    while IFS=: read -r special_round eps mapping; do
        if [ "$special_round" = "$round_num" ]; then
            episode_mapping="$mapping"
            total_episodes="$eps"
            break
        fi
    done < <(echo "$special_rounds" | tr '\n' '\r' | tr '\r' '\n')
    
    # If no special mapping found, use default
    if [ -z "$episode_mapping" ]; then
        episode_mapping="$default_mapping"
    fi
    
    # Determine session and episode number
    # Check for Race
    if [[ $filename == *[Rr][Aa][Cc][Ee]* ]] && [[ ! $filename == *[Dd][Aa][Yy]* ]]; then
        session="Race"
        # Look up episode number from mapping
        if [[ "$episode_mapping" == *"Race="* ]]; then
            episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Race=" | cut -d'=' -f2)
        else
            episode="4"  # Default if not found
        fi
    # Check for Qualifying
    elif [[ $filename == *[Qq]ualifying* ]]; then
        # Special handling for Indy 500 qualifying days
        if [[ $filename == *[Dd]ay[[:space:]]*1* ]] || [[ $filename == *[Dd]ay[[:space:]]*[Oo]ne* ]]; then
            session="Qualifying Day 1"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Qualifying Day 1="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Qualifying Day 1=" | cut -d'=' -f2)
            else
                episode="7"
            fi
        elif [[ $filename == *[Dd]ay[[:space:]]*2* ]] || [[ $filename == *[Dd]ay[[:space:]]*[Tt]wo* ]]; then
            session="Qualifying Day 2"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Qualifying Day 2="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Qualifying Day 2=" | cut -d'=' -f2)
            else
                episode="8"
            fi
        else
            session="Qualifying"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Qualifying="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Qualifying=" | cut -d'=' -f2)
            else
                episode="3"
            fi
        fi
    # Check for Practice
    elif [[ $filename == *FP* ]] || [[ $filename == *Practice* ]]; then
        if [[ $filename == *[Cc]arb[[:space:]]*[Dd]ay* ]]; then
            session="Carb Day"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Carb Day="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Carb Day=" | cut -d'=' -f2)
            else
                episode="9"
            fi
        elif [[ $filename == *[Ff][Pp]1* ]] || [[ $filename == *"Practice One"* ]] || [[ $filename == *"Practice 1"* ]]; then
            session="Free Practice 1"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 1="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 1=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 1="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 1=" | cut -d'=' -f2)
            else
                episode="1"
            fi
        elif [[ $filename == *[Ff][Pp]2* ]] || [[ $filename == *"Practice Two"* ]] || [[ $filename == *"Practice 2"* ]]; then
            session="Free Practice 2"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 2="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 2=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 2="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 2=" | cut -d'=' -f2)
            else
                episode="2"
            fi
        elif [[ $filename == *[Ff][Pp]3* ]] || [[ $filename == *"Practice Three"* ]] || [[ $filename == *"Practice 3"* ]]; then
            session="Free Practice 3"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 3="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 3=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 3="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 3=" | cut -d'=' -f2)
            else
                episode="3"
            fi
        elif [[ $filename == *[Ff][Pp]4* ]] || [[ $filename == *"Practice Four"* ]] || [[ $filename == *"Practice 4"* ]]; then
            session="Free Practice 4"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 4="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 4=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 4="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 4=" | cut -d'=' -f2)
            else
                episode="4"
            fi
        elif [[ $filename == *[Ff][Pp]5* ]] || [[ $filename == *"Practice Five"* ]] || [[ $filename == *"Practice 5"* ]]; then
            session="Free Practice 5"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 5="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 5=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 5="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 5=" | cut -d'=' -f2)
            else
                episode="5"
            fi
        elif [[ $filename == *[Ff][Pp]6* ]] || [[ $filename == *"Practice Six"* ]] || [[ $filename == *"Practice 6"* ]]; then
            session="Free Practice 6"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice 6="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice 6=" | cut -d'=' -f2)
            elif [[ "$episode_mapping" == *"Free Practice 6="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Free Practice 6=" | cut -d'=' -f2)
            else
                episode="6"
            fi
        else
            session="Practice"
            # Look up episode number from mapping
            if [[ "$episode_mapping" == *"Practice="* ]]; then
                episode=$(echo "$episode_mapping" | tr ',' '\n' | grep "Practice=" | cut -d'=' -f2)
            else
                episode="1"
            fi
        fi
    else
        session="Unknown"
        episode="0"
    fi

    # Format the output in a more readable way with clear sections
    echo "----------------------------------------"
    echo "🏍️ IndyCar Racing Processing Details:"
    echo "----------------------------------------"
    echo "🏁 Class: $sport_type"
    echo "📅 Year: $year"
    echo "🔄 Round: $round, Location: $location"
    echo "📺 Session: $session (S${round}E${episode})"
    echo "----------------------------------------"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/$sport_type $year"
    local round_dir="$season_dir/$round $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/${sport_type} ${year} - S${round}E${episode} - ${session}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "----------------------------------------"
        echo "✅ Successfully processed file!"
        echo "----------------------------------------"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed IndyCar Racing file</b><br><br>Class: ${sport_type}<br>Year: ${year}<br>Round: ${round} ${location}<br>Session: ${session} (S${round}E${episode})"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file" "Hardlink/Copy Error"
        fi
        ((error_count++))
        return 1
    fi

    return 0
}

# Function to process motorcycle racing files
process_moto_racing() {
    local file="$1"
    local filename=$(basename "$file")

    # Get class (MotoGP, Moto2, Moto3)
    local sport_type=""
    if [[ $filename =~ [Mm]oto[Gg][Pp][\.\-] ]]; then
        sport_type="MotoGP"
    elif [[ $filename =~ [Mm]oto2[\.\-] ]]; then
        sport_type="Moto2"
    elif [[ $filename =~ [Mm]oto3[\.\-] ]]; then
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
    if [[ $filename == *[Rr][Aa][Cc][Ee]* ]]; then
        session="Race"
        if [[ $sport_type == "MotoGP" ]]; then
            episode="6" # MotoGP races are episode 6 (after Sprint)
        else
            episode="5" # Moto2/3 races are episode 5 (no Sprint)
        fi
    # Check for Sprint - only for MotoGP class
    elif [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* ]]; then
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
    elif [[ $filename == *[Qq]ualifying* ]] || [[ $filename == *[Qq]1* ]] || [[ $filename == *[Qq]2* ]]; then
        if [[ $filename == *[Qq]1* ]]; then
            session="Qualifying 1"
            episode="3"
        elif [[ $filename == *[Qq]2* ]]; then
            session="Qualifying 2"
            episode="4"
        else
            session="Qualifying"
            episode="3"
        fi
    # Check for Practice
    elif [[ $filename == *FP* ]] || [[ $filename == *Practice* ]]; then
        if [[ $filename == *[Ff][Pp]1* ]] || [[ $filename == *"Practice One"* ]]; then
            session="Free Practice 1"
            episode="1"
        elif [[ $filename == *[Ff][Pp]2* ]] || [[ $filename == *"Practice Two"* ]]; then
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

    # Format the output in a more readable way with clear sections
    echo "----------------------------------------"
    echo "🏍️ Moto Racing Processing Details:"
    echo "----------------------------------------"
    echo "🏁 Class: $sport_type"
    echo "📅 Year: $year"
    echo "🔄 Round: $round, Location: $location"
    echo "📺 Session: $session (S${round}E${episode})"
    echo "----------------------------------------"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/$sport_type $year"
    local round_dir="$season_dir/$round $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/${sport_type} ${year} - S${round}E${episode} - ${session}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "----------------------------------------"
        echo "✅ Successfully processed file!"
        echo "----------------------------------------"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed Moto Racing file</b><br><br>Class: ${sport_type}<br>Year: ${year}<br>Round: ${round} ${location}<br>Session: ${session} (S${round}E${episode})" "Moto Racing Processing Complete"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file" "Hardlink/Copy Error"
        fi
        ((error_count++))
        return 1
    fi

    return 0
}

# Function to process Formula racing files
process_f1_racing() {
    local file="$1"
    local filename=$(basename "$file")
    
    # F1 Regular (Non-Sprint) Weekend Episode Order (2025):
    # E1: Drivers Press Conference
    # E2: Weekend Warm Up
    # E3: Free Practice 1
    # E4: Free Practice 2
    # E5: Free Practice 3
    # E6: Pre Qualifying Show
    # E7: Qualifying
    # E8: Post Qualifying Show
    # E9: Pre Race Show
    # E10: Race
    # E11: Post Race Show

    local sport_type=""
    # Update regex to match "Formula" followed by a number and a dot or other delimiter
    if [[ $filename =~ [Ff]ormula1[\.\-] ]]; then
        sport_type="Formula1"
    elif [[ $filename =~ [Ff]ormula2[\.\-] ]]; then
        sport_type="Formula2"
    elif [[ $filename =~ [Ff]ormula3[\.\-] ]]; then
        sport_type="Formula3"
    elif [[ $filename =~ [Ff]ormula[Ee][\.\-] ]]; then
        sport_type="Formula E"
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

    if [[ $sport_type == "Formula E" ]]; then
        # Check which type of race it is
        if [[ $filename =~ .*\.[Ff][Pp]1\. ]]; then
            session="Free Practice 1"
            episode="1"
        elif [[ $filename =~ .*\.[Ff][Pp]2\. ]]; then
            session="Free Practice 2"
            episode="2"
        elif [[ $filename =~ .*\.[Ff][Pp]3\. ]]; then
            session="Free Practice 3"
            episode="1"
        elif [[ $filename =~ .*\.[Ff][Pp]4\. ]]; then
            session="Free Practice 4"
            episode="2"
        elif [[ $filename =~ .*\.[Qq]ualifying\. ]]; then
            session="Qualifying"
            episode="3"
        elif [[ $filename =~ .*\.[Rr][Aa][Cc][Ee]\. ]]; then
            session="Race"
            episode="4"
        fi
    fi

    # Determine if this is a sprint weekend by checking if there's "Sprint" in the filename or location
    local is_sprint_weekend=false
    if [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* && $sport_type == "Formula1" ]]; then
        is_sprint_weekend=true
        echo "Detected Sprint weekend format"
    fi
    
    # Specific sprint weekend locations - 2025 Sprint locations
    if [[ $location == "USA" || $location == "Miami" || $location == "China" || $location == "Austin" || 
          $location == "Qatar" || $location == "Belgium" || $location == "Brazil" ]]; then
        is_sprint_weekend=true
        echo "Detected Sprint weekend location: $location"
    fi

    # Determine session and episode
    local session=""
    local episode=""

    # Check for Drivers Press Conference
    if [[ $filename == *[Dd]rivers*[Pp]ress*[Cc]onference* ]]; then
        session="Drivers Press Conference"
        if [[ $sport_type == "Formula1" ]]; then
            episode="1"
        fi
    # Check for Weekend Warm Up
    elif [[ $filename == *[Ww]eekend*[Ww]arm* ]]; then
        session="Weekend Warm Up"
        if [[ $sport_type == "Formula1" ]]; then
            episode="2"
        fi
    # Check for Practice
    elif [[ $filename =~ .*\.[Ff][Pp]1\. && $sport_type != "Formula E" ]] || [[ $filename =~ .*\.[Ff][Pp]2\. && $sport_type != "Formula E" ]] || [[ $filename =~ .*\.[Ff][Pp]3\. && $sport_type != "Formula E" ]] || [[ $filename == *Practice* ]]; then
        if [[ ($filename =~ .*\.[Ff][Pp]1\. || $filename == *"Practice One"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 1"
            episode="3"
        elif [[ ($filename =~ .*\.[Ff][Pp]2\. || $filename == *"Practice Two"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 2"
            episode="4"
        elif [[ ($filename =~ .*\.[Ff][Pp]3\. || $filename == *"Practice Three"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 3"
            episode="5"
        else
            session="Free Practice"
            episode="3"
        fi
    # Check for Sprint
    elif [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* && $sport_type != "Formula E" ]]; then
        # F1 Sprint Weekend Episode Order (2025):
        # E1: Drivers Press Conference
        # E2: Weekend Warm Up
        # E3: Free Practice 1
        # E4: Sprint Qualifying (Previously known as Sprint Shootout)
        # E5: Pre Sprint Show
        # E6: Sprint
        # E7: Post Sprint Show
        # E8: Pre Qualifying Show
        # E9: Qualifying
        # E10: Post Qualifying Show
        # E11: Pre Race Show
        # E12: Race
        # E13: Post Race Show
        if [[ $filename == *[Pp][Rr][Ee]*[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $filename == *[Qq]ualifying* ]]; then
            session="Pre Sprint Show"
            episode="5"
        elif [[ $filename == *[Pp][Oo][Ss][Tt]*[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $filename == *[Qq]ualifying* ]]; then
            session="Post Sprint Show"
            episode="7"
        elif [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* ]]; then
            session="Sprint Qualifying"
            episode="4"
        elif [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $filename == *[Qq]ualifying* && ! $filename == *[Ss][Hh][Oo][Ww]* && $sport_type == "Formula1" ]]; then
            session="Sprint"
            episode="6" 
        else
            # For Formula classes that don't have Sprint races
            session="Unknown" 
            episode="0"
            echo "Warning: Sprint session found for $sport_type which shouldn't have sprints"
        fi
    # Check for Qualifying
    elif [[ $filename == *[Qq]ualifying* && $sport_type != "Formula E" ]]; then
        # Make sure we're not processing a file that has already been handled in the Sprint section
        if [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* ]]; then
            # Skip if already processed as Sprint Qualifying
            :
        elif [[ $filename == *[Pp][Rr][Ee]*[Qq]ualifying* ]]; then
            session="Pre Qualifying Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="8"  # Sprint weekend
            else
                episode="6"  # Regular weekend
            fi
        elif [[ $filename == *[Pp][Oo][Ss][Tt]*[Qq]ualifying* ]]; then
            session="Post Qualifying Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="10"  # Sprint weekend
            else
                episode="8"  # Regular weekend
            fi
        else
            session="Qualifying"
            if [[ $sport_type == "Formula1" ]]; then
                if [[ $is_sprint_weekend == true ]]; then
                    episode="9"  # Sprint weekend
                else
                    episode="7"  # Regular weekend
                fi
            else
                episode="2"
            fi
        fi
    # Check for Race
    elif [[ $filename == *Race* && $sport_type != "Formula E" ]]; then
        # First, handle sprint-related race files, which should take precedence
        if [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Rr][Aa][Cc][Ee]* ]]; then
            session="Sprint Race"
            episode="6"
        # Then handle pre/post race show files, excluding sprint-related ones
        elif [[ $filename == *[Pp][Rr][Ee]*[Rr][Aa][Cc][Ee]* && ! $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* ]]; then
            session="Pre Race Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="11"  # Sprint weekend
            else
                episode="9"  # Regular weekend
            fi
        elif [[ $filename == *[Pp][Oo][Ss][Tt]*[Rr][Aa][Cc][Ee]* && ! $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* ]]; then
            session="Post Race Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="13"  # Sprint weekend
            else
                episode="11"  # Regular weekend
            fi
        elif [[ $filename == *[Ff][Ee][Aa][Tt][Uu][Rr][Ee]*[Rr][Aa][Cc][Ee]* ]]; then
            session="Feature Race"
            episode="4"
        # Finally handle the main race
        else
            session="Race"
            if [[ $sport_type == "Formula1" ]]; then
                if [[ $is_sprint_weekend == true ]]; then
                    episode="12"  # Sprint weekend
                else
                    episode="10"  # Regular weekend
                fi
            else
                episode="3"
            fi
        fi
    else
        session="Unknown"
        episode="0"
    fi

    # Format the output in a more readable way with clear sections
    echo "----------------------------------------"
    echo "🏎️ Formula Racing Processing Details:"
    echo "----------------------------------------"
    echo "🏁 Class: $sport_type"
    echo "📅 Year: $year"
    echo "🔄 Round: $round, Location: $location"
    echo "📺 Session: $session (S${round}E${episode})"
    echo "----------------------------------------"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/$sport_type $year"
    local round_dir="$season_dir/$round $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/${sport_type} ${year} - S${round}E${episode} - ${session}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "----------------------------------------"
        echo "✅ Successfully processed file!"
        echo "----------------------------------------"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed Formula Racing file</b><br><br>Class: ${sport_type}<br>Year: ${year}<br>Round: ${round} ${location}<br>Session: ${session} (S${round}E${episode})" "Formula Racing Processing Complete"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file for ${sport_type}" "Hardlink/Copy Error"
        fi
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
    if [[ ! $filename =~ ^[uU][fF][cC][\.\-] ]]; then
        echo "Not a UFC file: $filename"
        ((error_count++))
        return 1
    fi

    # Parse UFC number (season) and event name in one regex
    local season=""
    local event_name=""
    
    # This regex captures the UFC number and event name before any of the episode types
    if [[ $filename =~ ^ufc\.([0-9]+)\.(.+?)\.(early\.prelims|prelims|ppv)\. ]]; then
        season="${BASH_REMATCH[1]}"
        event_name="${BASH_REMATCH[2]}"
        
        # Remove any trailing "early" from the event name
        event_name="${event_name%.early}"
        
        # Replace dots with spaces for readability
        event_name="${event_name//./ }"
    else
        echo "Could not parse UFC number and event name from filename: $filename"
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

    # Format the output in a more readable way with clear sections
    echo "----------------------------------------"
    echo "📊 UFC File Processing Details:"
    echo "----------------------------------------"
    echo "🏆 Season: UFC $season"
    echo "🥊 Event: $event_name"
    echo "📺 Episode: $episode_type (${season}x${episode_num})"
    echo "----------------------------------------"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local season_dir="$DEST_DIR/UFC"
    local event_dir="$season_dir/Season $season"
    mkdir -p "$event_dir"

    # Create the target filename
    local target_file="$event_dir/UFC - S${season}E${episode_num} - $episode_type.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "Successfully processed file!"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed UFC file:</b><br><br>Season: ${season}<br>Event: ${event_name}<br>Episode: ${episode_type} (${season}x${episode_num})" "UFC Processing Complete"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file" "Hardlink/Copy Error"
        fi
        ((error_count++))
        return 1
    fi

    return 0
}

# Function to process Isle of Man TT racing files
process_isle_of_man_tt() {
    local file="$1"
    local filename=$(basename "$file")

    # Skip sample files
    if [[ $filename == *sample* ]]; then
        echo "Skipping sample file: $filename"
        ((skipped_count++))
        return 0
    fi

    # Check if it's an Isle of Man TT file
    if [[ ! $filename =~ ^Isle\.Of\.Man\.TT\. ]]; then
        echo "Not an Isle of Man TT file: $filename"
        ((error_count++))
        return 1
    fi

    # Extract year from filename
    local year=""
    if [[ $filename =~ ^Isle\.Of\.Man\.TT\.([0-9]{4})\. ]]; then
        year="${BASH_REMATCH[1]}"
    else
        echo "Could not parse year from filename: $filename"
        ((error_count++))
        return 1
    fi

    # Determine race type and episode number
    local race_type=""
    local episode_num=""
    local season="1" # Isle of Man TT is typically one season per year

    # Map race types to episode numbers
    if [[ $filename == *Qualifying.Highlights* ]]; then
        race_type="Qualifying Highlights"
        episode_num="1"
    elif [[ $filename == *Superbike.Race* ]]; then
        race_type="Superbike Race"
        episode_num="2"
    elif [[ $filename == *Supersport.Race.One* ]]; then
        race_type="Supersport Race One"
        episode_num="3"
    elif [[ $filename == *Sidecar.Race.One* ]]; then
        race_type="Sidecar Race One"
        episode_num="4"
    elif [[ $filename == *Supertwin.Race.One* ]]; then
        race_type="Supertwin Race One"
        episode_num="5"
    elif [[ $filename == *Superstock.Race.One* ]]; then
        race_type="Superstock Race One"
        episode_num="6"
    elif [[ $filename == *Supersport.Race.Two* ]]; then
        race_type="Supersport Race Two"
        episode_num="7"
    elif [[ $filename == *Sidecar.Race.Two* ]]; then
        race_type="Sidecar Race Two"
        episode_num="8"
    elif [[ $filename == *Supertwin.Race.Two* ]] && [[ $year == "2024" ]]; then
        race_type="Supertwin Race Two"
        episode_num="9"
    elif [[ $filename == *Senior.TT.Race* ]] && [[ $year == "2024" ]]; then
        race_type="Senior TT Race"
        episode_num="10"
    elif [[ $filename == *Superstock.Race.Two* ]] && [[ $year == "2025" ]]; then
        race_type="Superstock Race Two"
        episode_num="9"
    elif [[ $filename == *Supertwin.Race.Two* ]] && [[ $year == "2025" ]]; then
        race_type="Supertwin Race Two"
        episode_num="10"
    elif [[ $filename == *Senior.TT.Race* ]] && [[ $year == "2025" ]]; then
        race_type="Senior TT Race"
        episode_num="11"
    else
        echo "Unknown race type in filename: $filename"
        race_type="Unknown Race"
        episode_num="0"
    fi

    # Format the output in a more readable way with clear sections
    echo "----------------------------------------"
    echo "🏍️ Isle of Man TT Processing Details:"
    echo "----------------------------------------"
    echo "📅 Year: $year"
    echo "🏁 Race: $race_type"
    echo "📺 Episode: $episode_num"
    echo "----------------------------------------"

    # Get file extension (check for common video extensions)
    local extension=""
    if [[ $filename =~ \.(mkv|mp4|avi|mov)$ ]]; then
        extension="${BASH_REMATCH[1]}"
    else
        # Default to mp4 if no extension found
        extension="mp4"
    fi

    # Create target directories
    local season_dir="$DEST_DIR/Isle of Man TT/Season $year"
    mkdir -p "$season_dir"

    # Create the target filename
    local target_file="$season_dir/Isle of Man TT - S${year}E${episode_num} - ${race_type}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "----------------------------------------"
        echo "✅ Successfully processed file!"
        echo "----------------------------------------"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed Isle of Man TT file</b><br><br>Year: ${year}<br>Race: ${race_type}<br>Episode: S${season}E${episode_num}" "Isle of Man TT Processing Complete"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file" "Hardlink/Copy Error"
        fi
        ((error_count++))
        return 1
    fi

    return 0
}

# Function to process World Superbike files
process_world_superbike() {
    local file="$1"
    local filename=$(basename "$file")

    # Skip sample files
    if [[ $filename == *sample* ]]; then
        echo "Skipping sample file: $filename"
        ((skipped_count++))
        return 0
    fi

    # Determine championship type and validate file
    local championship=""
    local championship_full=""
    if [[ $filename =~ ^WSBK\. ]]; then
        championship="WSBK"
        championship_full="World Superbike"
    elif [[ $filename =~ ^WSSP300\. ]]; then
        championship="WSSP300"
        championship_full="World Supersport 300"
    elif [[ $filename =~ ^WSSP\. ]]; then
        championship="WSSP"
        championship_full="World Supersport"
    else
        echo "Not a World Superbike Championship file: $filename"
        ((error_count++))
        return 1
    fi

    # Extract year and round from filename
    local year=""
    local round=""
    local location=""
    if [[ $filename =~ ^${championship}\.([0-9]{4})\.Round([0-9]{2})\.(.+?)\.(Race|FP|Superpole|Warm|Weekend) ]]; then
        year="${BASH_REMATCH[1]}"
        round="${BASH_REMATCH[2]}"
        location="${BASH_REMATCH[3]}"
        location="${location//./ }"  # Replace dots with spaces
    else
        echo "Could not parse year and round from filename: $filename"
        ((error_count++))
        return 1
    fi

    # Handle season preview for WSBK
    if [[ $championship == "WSBK" && $filename == *Season.Preview* ]]; then
        round="0"
        location="Pre-Season Testing"
    fi

    # Determine session type and episode number
    local session_type=""
    local episode_num=""

    # Different episode numbering based on championship
    if [[ $championship == "WSBK" ]]; then
        if [[ $filename == *Season.Preview* ]]; then
            session_type="Season Preview"
            episode_num="1"
        elif [[ $filename == *[Ff][Pp]1* ]]; then
            session_type="Free Practice 1"
            episode_num="1"
        elif [[ $filename == *[Ff][Pp]2* ]]; then
            session_type="Free Practice 2"
            episode_num="2"
        elif [[ $filename == *[Ff][Pp]3* ]]; then
            session_type="Free Practice 3"
            episode_num="3"
        elif [[ $filename == *Superpole* && ! $filename == *Race* ]]; then
            session_type="Superpole"
            episode_num="4"
        elif [[ $filename == *Race.One* ]]; then
            session_type="Race One"
            episode_num="5"
        elif [[ $filename == *Warm.Up* ]]; then
            session_type="Warm Up"
            episode_num="6"
        elif [[ $filename == *Superpole.Race* ]]; then
            session_type="Superpole Race"
            episode_num="7"
        elif [[ $filename == *Race.Two* ]]; then
            session_type="Race Two"
            episode_num="8"
        elif [[ $filename == *Weekend.Highlights* ]]; then
            session_type="Weekend Highlights"
            episode_num="9"
        fi
    else  # WSSP and WSSP300
        if [[ $filename == *[Ff][Pp]1* ]]; then
            session_type="Free Practice"
            episode_num="1"
        elif [[ $filename == *Superpole* ]]; then
            session_type="Superpole"
            episode_num="2"
        elif [[ $filename == *Warm.Up.One* ]]; then
            session_type="Warm Up One"
            episode_num="3"
        elif [[ $filename == *Race.One* ]]; then
            session_type="Race One"
            episode_num="4"
        elif [[ $filename == *Warm.Up.Two* ]]; then
            session_type="Warm Up Two"
            episode_num="5"
        elif [[ $filename == *Race.Two* ]]; then
            session_type="Race Two"
            episode_num="6"
        fi
    fi

    # Format the output
    echo "----------------------------------------"
    echo "🏍️ ${championship_full} Processing Details:"
    echo "----------------------------------------"
    echo "📅 Year: $year"
    echo "🔄 Round: $round $location"
    echo "📺 Session: $session_type (${round}x${episode_num})"
    echo "----------------------------------------"

    # Get file extension
    local extension=""
    if [[ $filename =~ \.(mkv|mp4|avi|mov)$ ]]; then
        extension="${BASH_REMATCH[1]}"
    else
        extension="mp4"
    fi

    # Create target directories
    local season_dir="$DEST_DIR/$championship_full $year"
    local round_dir="$season_dir/Round $round - $location"
    mkdir -p "$round_dir"

    # Create the target filename
    local target_file="$round_dir/$championship_full $year - S${round}E${episode_num} - ${session_type}.${extension}"

    # Check if file already exists
    if [[ -f "$target_file" ]]; then
        echo "File already exists at destination: $target_file - skipping"
        ((skipped_count++))
        return 0
    fi

    echo "🚚 Moving"
    echo "From: $file" 
    echo "To: $target_file"
    # Create hardlink instead of moving
    if ln "$file" "$target_file" 2>/dev/null || cp "$file" "$target_file"; then
        echo "----------------------------------------"
        echo "✅ Successfully processed file!"
        echo "----------------------------------------"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_notification "<b>✅ Processed ${championship_full} file</b><br><br>Year: ${year}<br>Round: ${round} ${location}<br>Session: ${session_type} (${round}x${episode_num})" "${championship_full} Processing Complete"
        fi
        ((processed_count++))
    else
        echo "Error: Failed to create hardlink or copy file"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "❌ Failed to create hardlink or copy file" "Hardlink/Copy Error"
        fi
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

echo "Looking for ALL existing sports files (MKV, MP4, and directories)..."
while IFS= read -r file; do
    echo ""
    echo "================================================"
    echo "Found item: $file"
    organize_sports "$file"
done < <(find "$SRC_DIR" \( -type f \( -name "*.mkv" -o -name "*.mp4" \) -o -type d \))

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
    echo "DEBUG: Running find command for new MKV and MP4 files..."
    
    # Check for new files periodically - only files modified in the last interval
    find_output=$(find "$SRC_DIR" \( -type f \( -name "*.mkv" -o -name "*.mp4" \) -o -type d \) -mmin -$((PROCESS_INTERVAL/60+1)) 2>&1)
    find_status=$?
    
    if [ $find_status -ne 0 ]; then
        echo "ERROR: Find command failed with status $find_status: $find_output"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "<b>❌ Find Command Failed</b><br><br>Status: $find_status<br>Error: $find_output" "Sports Organizer Error"
        fi
    else
        echo "DEBUG: Find command completed successfully"
    fi
    
    echo "$find_output" | while read file; do
        if [ -n "$file" ]; then
            echo "Found new MKV file: $file"
            organize_sports "$file"
            
            # We'll let the individual processing functions handle their own notifications
        fi
    done
    
    # Print periodic summary if files were processed
    if [ $((processed_count + skipped_count + error_count)) -gt 0 ]; then
        echo "Summary for this check cycle:"
        print_summary
        
        # Send a nicely formatted summary notification
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            summary_html="<b>📊 Sports Organizer Summary</b><br><br>"
            summary_html+="<b>✅ Processed:</b> $processed_count files<br>"
            summary_html+="<b>⏭️ Skipped:</b> $skipped_count files<br>"
            
            if [ $error_count -gt 0 ]; then
                summary_html+="<b>❌ Errors:</b> $error_count files"
            else
                summary_html+="<b>✓ No errors encountered</b>"
            fi
            
            send_pushover_notification "$summary_html" "Sports Organizer Summary"
        fi
        
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
