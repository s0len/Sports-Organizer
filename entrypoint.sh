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
    # Check for Women's UEFA Euro
    elif [[ $filename =~ ^Womens\.UEFA\.Euro\. ]] && [[ $filename =~ \.(mkv|mp4)$ ]]; then
        process_womens_uefa_euro "$file"
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
    local dirname=$(dirname "$file")

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

    # Parse filename - detect format type
    IFS='.' read -ra PARTS <<<"$filename"
    local year=""
    local round=""
    local location=""
    
    # Check if this is the old format: MotoGP.YEAR.RoundXX.Location.Session
    if [[ ${PARTS[2]} =~ ^[Rr]ound[0-9]+ ]]; then
        # Old format
        if [[ ${#PARTS[@]} -lt 4 ]]; then
            echo "Not enough parts in old format filename: $filename"
            ((error_count++))
            return 1
        fi
        
        year="${PARTS[1]}"
        round="${PARTS[2]#Round}" # Remove 'Round' prefix
        round="${round#round}"    # Remove lowercase 'round' prefix too
        location="${PARTS[3]}"
        echo "Detected old format"
    else
        # New format: motogp.YEAR.location.session.details...
        if [[ ${#PARTS[@]} -lt 4 ]]; then
            echo "Not enough parts in new format filename: $filename"
            ((error_count++))
            return 1
        fi
        
        year="${PARTS[1]}"
        location="${PARTS[2]}"
        
        # Try to extract round number from directory name
        local dir_basename=$(basename "$dirname")
        if [[ $dir_basename =~ [Rr]ound([0-9]+) ]]; then
            round="${BASH_REMATCH[1]}"
            echo "Extracted round from directory: $round"
        else
            # Fallback: use location as round identifier
            round="$(echo "$location" | tr '[:lower:]' '[:upper:]')"
            echo "Using location as round identifier: $round"
        fi
        
        # Capitalize location for consistency
        location="$(echo "$location" | sed 's/\b\w/\U&/g')"
        echo "Detected new format"
    fi

    echo "Year: $year"
    echo "Round: $round, Location: $location"

    # Determine session and episode
    local session=""
    local episode=""

    # For new format, session info is in PARTS[3] and details in PARTS[4]
    local session_part=""
    local session_detail=""
    if [[ ${PARTS[2]} =~ ^[Rr]ound[0-9]+ ]]; then
        # Old format - session info throughout filename
        session_part="$filename"
    else
        # New format - session info in specific parts
        session_part="${PARTS[3]}"
        if [[ ${#PARTS[@]} -gt 4 ]]; then
            session_detail="${PARTS[4]}"
        fi
    fi

    # Check for Race
    if [[ $session_part == *[Rr][Aa][Cc][Ee]* ]]; then
        session="Race"
        if [[ $sport_type == "MotoGP" ]]; then
            episode="6" # MotoGP races are episode 6 (after Sprint)
        else
            episode="5" # Moto2/3 races are episode 5 (no Sprint)
        fi
    # Check for Sprint - only for MotoGP class
    elif [[ $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]* ]]; then
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
    elif [[ $session_part == *[Qq]ualifying* ]] || [[ $session_part == *[Qq]1* ]] || [[ $session_part == *[Qq]2* ]]; then
        # Check session detail for specific qualifying number
        if [[ $session_detail == *[Oo]ne* ]] || [[ $session_detail == *1* ]] || [[ $session_part == *[Qq]1* ]]; then
            session="Qualifying 1"
            episode="3"
        elif [[ $session_detail == *[Tt]wo* ]] || [[ $session_detail == *2* ]] || [[ $session_part == *[Qq]2* ]]; then
            session="Qualifying 2"
            episode="4"
        else
            session="Qualifying"
            episode="3"
        fi
    # Check for Practice
    elif [[ $session_part == *[Ff][Pp]* ]] || [[ $session_part == *[Pp]ractice* ]]; then
        # Check session detail for specific practice number
        if [[ $session_detail == *[Oo]ne* ]] || [[ $session_detail == *1* ]] || [[ $session_part == *[Ff][Pp]1* ]] || [[ $session_part == *"Practice One"* ]]; then
            session="Free Practice 1"
            episode="1"
        elif [[ $session_detail == *[Tt]wo* ]] || [[ $session_detail == *2* ]] || [[ $session_part == *[Ff][Pp]2* ]] || [[ $session_part == *"Practice Two"* ]]; then
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
    # Check for MWR space-separated format first (e.g. "Formula1 2025 Round14...")
    if [[ $filename =~ ^[Ff]ormula1\ [0-9] ]]; then
        sport_type="Formula1"
    elif [[ $filename =~ ^[Ff]ormula2\ [0-9] ]]; then
        sport_type="Formula2"
    elif [[ $filename =~ ^[Ff]ormula3\ [0-9] ]]; then
        sport_type="Formula3"
    elif [[ $filename =~ ^[Ff]ormula[Ee]\ [0-9] ]]; then
        sport_type="Formula E"
    # Standard dot/dash separated format (e.g. "Formula1.2024.Round01...")
    elif [[ $filename =~ [Ff]ormula1[\.\-] ]]; then
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

    # Parse filename based on format detected
    local year=""
    local round=""
    local location=""
    local is_f1carreras=false
    local is_verum_format=false
    
    # Split filename into parts - check if it uses spaces or dots
    local PARTS=()
    local is_space_format=false
    
    # Check if this is new MWR format with spaces (Formula1 2025 Round14 Hungary...)
    if [[ $filename =~ ^[Ff]ormula[1-3Ee]\ [0-9]{4}\ Round[0-9]+ ]]; then
        is_space_format=true
        # Split on spaces for new MWR format
        IFS=' ' read -ra PARTS <<<"$filename"
    else
        # Split on dots for traditional format
        IFS='.' read -ra PARTS <<<"$filename"
    fi
    
    # Check if this is F1Carreras format (has S20XXE pattern)
    if [[ ${PARTS[1]} =~ ^S([0-9]{4})E[0-9]+ ]]; then
        is_f1carreras=true
        year="${BASH_REMATCH[1]}"
        
        # F1Carreras format: Formula1.S2025E66.Round12.UnitedKingdom.Race.F1TV...
        if [[ ${#PARTS[@]} -lt 5 ]]; then
            echo "Not enough parts in F1Carreras filename: $filename"
            ((error_count++))
            return 1
        fi
        
        round="${PARTS[2]#Round}" # Remove 'Round' prefix  
        location="${PARTS[3]}"
        echo "F1Carreras format detected"
    # Check if this is VERUM format (lowercase formula1.year.location.grand.prix[.session])
    elif [[ ${PARTS[0]} == "formula1" && ${PARTS[1]} =~ ^[0-9]{4}$ && ${PARTS[3]} == "grand" && ${PARTS[4]} == "prix" ]]; then
        is_verum_format=true
        year="${PARTS[1]}"
        location="${PARTS[2]}"
        
        # Capitalize location for consistency
        location="$(echo "$location" | sed 's/\b\w/\U&/g')"
        
        # Map location to round number (2025 F1 calendar)
        case "$location" in
            "Australian") round="01" ;;
            "Chinese") round="02"; location="China" ;;
            "Japanese") round="03" ;;
            "Bahrain") round="04" ;;
            "Saudi") round="05" ;;
            "Miami") round="06" ;;
            "Emilia") round="07" ;;
            "Monaco") round="08" ;;
            "Spanish") round="09" ;;
            "Canadian") round="10" ;;
            "Austrian") round="11" ;;
            "British") round="12" ;;
            "Hungarian") round="14" ;;
            "Belgian") round="13"; location="Belgium" ;;
            "Dutch") round="15" ;;
            "Italian") round="16" ;;
            "Azerbaijan") round="17" ;;
            "Singapore") round="18" ;;
            "United") round="19" ;;  # United States (Austin)
            "Mexican") round="20" ;;
            "Brazilian") round="21"; location="Brazil" ;;
            "Las") round="22" ;;     # Las Vegas
            "Qatar") round="23" ;;
            "Abu") round="24" ;;     # Abu Dhabi
            *)
                # Fallback: use a sequential number or location as round
                round="$(echo "$location" | tr '[:lower:]' '[:upper:]')"
                echo "Warning: Unknown location '$location', using location as round identifier"
                ;;
        esac
        
        echo "VERUM format detected"
    elif [[ $is_space_format == true ]]; then
        # New MWR format with spaces: Formula1 2025 Round14 Hungary Qualifying...
        if [[ ${#PARTS[@]} -lt 4 ]]; then
            echo "Not enough parts in new MWR space format filename: $filename"
            ((error_count++))
            return 1
        fi
        
        year="${PARTS[1]}"
        round="${PARTS[2]#Round}" # Remove 'Round' prefix
        location="${PARTS[3]}"
        echo "New MWR space format detected"
    else
        # Original MWR format: Formula1.YEAR.RoundXX.Location.Session
        if [[ ${#PARTS[@]} -lt 4 ]]; then
            echo "Not enough parts in MWR filename: $filename"
            ((error_count++))
            return 1
        fi
        
        year="${PARTS[1]}"
        round="${PARTS[2]#Round}" # Remove 'Round' prefix
        location="${PARTS[3]}"
        echo "MWR format detected"
    fi
    
    echo "Year: $year"
    echo "Round: $round, Location: $location"

    # Determine session and episode
    local session=""
    local episode=""

    if [[ $sport_type == "Formula E" ]]; then
        # Debug output for Formula E files
        if [ "$DEBUG" = "true" ]; then
            echo "DEBUG: Formula E file detected with filename: $filename"
        fi
        
        # Check which type of race it is
        if [[ $filename =~ ([Ff][Pp]1|[Ff]ree[\.\ ][Pp]ractice[\.\ ]1) ]]; then
            session="Free Practice 1"
            episode="1"
        elif [[ $filename =~ ([Ff][Pp]2|[Ff]ree[\.\ ][Pp]ractice[\.\ ]2) ]]; then
            session="Free Practice 2"
            episode="2"
        elif [[ $filename =~ ([Ff][Pp]3|[Ff]ree[\.\ ][Pp]ractice[\.\ ]3) ]]; then
            session="Free Practice 3"
            episode="1"
        elif [[ $filename =~ ([Ff][Pp]4|[Ff]ree[\.\ ][Pp]ractice[\.\ ]4) ]]; then
            session="Free Practice 4"
            episode="2"
        elif [[ $filename =~ [Qq]ualifying ]]; then
            session="Qualifying"
            episode="3"
        elif [[ $filename =~ [Rr]ace ]]; then
            session="Race"
            episode="4"
        else
            # Default case if no session type is detected
            echo "WARNING: Unable to determine session type for Formula E file: $filename"
            session="Unknown"
            episode="0"
        fi
        
        if [ "$DEBUG" = "true" ]; then
            echo "DEBUG: Assigned session=$session and episode=$episode"
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

    # For F1Carreras format, get session from specific part position
    local session_part=""
    if [[ $is_f1carreras == true ]]; then
        session_part="${PARTS[4]}"
        echo "F1Carreras session part: $session_part"
    elif [[ $is_verum_format == true ]]; then
        # For VERUM format, session info is in PARTS[5] and potentially PARTS[6]
        if [[ ${#PARTS[@]} -gt 5 && ${PARTS[5]} != "1080p" && ${PARTS[5]} != "720p" ]]; then
            session_part="${PARTS[5]}"
            # Check if there's a continuation (like "sprint race")
            if [[ ${#PARTS[@]} -gt 6 && ${PARTS[6]} == "race" ]]; then
                session_part="${PARTS[5]}.${PARTS[6]}"
            fi
        else
            # No session part means it's the main race
            session_part="race"
        fi
        echo "VERUM session part: $session_part"
    elif [[ $is_space_format == true ]]; then
        # For new MWR space format, session info is in PARTS[4]
        if [[ ${#PARTS[@]} -gt 4 ]]; then
            session_part="${PARTS[4]}"
            echo "New MWR space format session part: $session_part"
        fi
    fi

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
    # Check for Practice sessions (handle both dot format ".FP1." and space format " FP1 ")
    elif [[ $sport_type != "Formula E" ]] && ([[ $filename == *Practice* ]] || [[ $session_part == *Practice* ]] || [[ $session_part == *practice* ]] || [[ $filename == *FP1* ]] || [[ $filename == *FP2* ]] || [[ $filename == *FP3* ]]); then
        if [[ ($filename == *FP1* || $filename == *"Practice One"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 1"
            episode="3"
        elif [[ ($filename == *FP2* || $filename == *"Practice Two"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 2"
            episode="4"
        elif [[ ($filename == *FP3* || $filename == *"Practice Three"*) && $sport_type == "Formula1" ]]; then
            session="Free Practice 3"
            episode="5"
        else
            session="Free Practice"
            episode="3"
        fi
    # Check for Sprint
    elif [[ ($filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *sprint*) && $sport_type != "Formula E" ]]; then
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
        if [[ ($filename == *[Pp][Rr][Ee]*[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *[Pp][Rr][Ee]*[Ss][Pp][Rr][Ii][Nn][Tt]*) && ! $filename == *[Qq]ualifying* && ! $session_part == *[Qq]ualifying* ]]; then
            session="Pre Sprint Show"
            episode="5"
        elif [[ ($filename == *[Pp][Oo][Ss][Tt]*[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *[Pp][Oo][Ss][Tt]*[Ss][Pp][Rr][Ii][Nn][Tt]*) && ! $filename == *[Qq]ualifying* && ! $session_part == *[Qq]ualifying* ]]; then
            session="Post Sprint Show"
            episode="7"
        elif [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* || $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* || $session_part == *sprint*qualifying* ]]; then
            session="Sprint Qualifying"
            episode="4"
        elif [[ ($filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]* || $session_part == *sprint.race* || $session_part == *sprint*) && ! $filename == *[Qq]ualifying* && ! $session_part == *[Qq]ualifying* && ! $filename == *[Ss][Hh][Oo][Ww]* && ! $session_part == *[Ss][Hh][Oo][Ww]* && $sport_type == "Formula1" ]]; then
            session="Sprint"
            episode="6" 
        else
            # For Formula classes that don't have Sprint races
            session="Unknown" 
            episode="0"
            echo "Warning: Sprint session found for $sport_type which shouldn't have sprints"
        fi
    # Check for Qualifying
    elif [[ ($filename == *[Qq]ualifying* || $session_part == *[Qq]ualifying* || $session_part == *qualifying*) && $sport_type != "Formula E" ]]; then
        # Make sure we're not processing a file that has already been handled in the Sprint section
        if [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* || $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Qq]ualifying* || $session_part == *sprint*qualifying* ]]; then
            # Skip if already processed as Sprint Qualifying
            :
        elif [[ $filename == *[Pp][Rr][Ee]*[Qq]ualifying* || $session_part == *[Pp][Rr][Ee]*[Qq]ualifying* ]]; then
            session="Pre Qualifying Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="8"  # Sprint weekend
            else
                episode="6"  # Regular weekend
            fi
        elif [[ $filename == *[Pp][Oo][Ss][Tt]*[Qq]ualifying* || $session_part == *[Pp][Oo][Ss][Tt]*[Qq]ualifying* ]]; then
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
    elif [[ ($filename == *Race* || $session_part == *Race* || $session_part == *race* || $session_part == "race") && $sport_type != "Formula E" ]]; then
        # First, handle sprint-related race files, which should take precedence
        if [[ $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Rr][Aa][Cc][Ee]* || $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]*[Rr][Aa][Cc][Ee]* || $session_part == *sprint.race* ]]; then
            session="Sprint"
            episode="6"
        # Then handle pre/post race show files, excluding sprint-related ones
        elif [[ ($filename == *[Pp][Rr][Ee]*[Rr][Aa][Cc][Ee]* || $session_part == *[Pp][Rr][Ee]*[Rr][Aa][Cc][Ee]*) && ! $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $session_part == *sprint* ]]; then
            session="Pre Race Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="11"  # Sprint weekend
            else
                episode="9"  # Regular weekend
            fi
        elif [[ ($filename == *[Pp][Oo][Ss][Tt]*[Rr][Aa][Cc][Ee]* || $session_part == *[Pp][Oo][Ss][Tt]*[Rr][Aa][Cc][Ee]*) && ! $filename == *[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $session_part == *[Ss][Pp][Rr][Ii][Nn][Tt]* && ! $session_part == *sprint* ]]; then
            session="Post Race Show"
            if [[ $is_sprint_weekend == true ]]; then
                episode="13"  # Sprint weekend
            else
                episode="11"  # Regular weekend
            fi
        elif [[ $filename == *[Ff][Ee][Aa][Tt][Uu][Rr][Ee]*[Rr][Aa][Cc][Ee]* || $session_part == *[Ff][Ee][Aa][Tt][Uu][Rr][Ee]*[Rr][Aa][Cc][Ee]* ]]; then
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
    if [[ $filename == *Qualifying.Highlights.Part.Two* ]]; then
        race_type="Qualifying Highlights Part Two"
        episode_num="2"
    elif [[ $filename == *Qualifying.Highlights.Part.Three* ]]; then
        race_type="Qualifying Highlights Part Three"
        episode_num="3"
    elif [[ $filename == *Qualifying.Highlights* ]]; then
        race_type="Qualifying Highlights Part One"
        episode_num="1"
    elif [[ $filename == *Superbike.Race.Highlights* ]]; then
        race_type="Superbike Race One Highlights"
        episode_num="4"
    elif [[ $filename == *Supersport.Race.One.Highlights* ]]; then
        race_type="Supersport Race One Highlights"
        episode_num="5"
    elif [[ $filename == *Supersport.Race.Two.Highlights* ]]; then
        race_type="Supersport Race Two Highlights"
        episode_num="6"
    elif [[ $filename == *Superstock.Race.Two.Highlights* ]]; then
        race_type="Superstock Race Two Highlights"
        episode_num="7"
    elif [[ $filename == *Supertwin.Race.Two.Highlights* ]]; then
        race_type="Supertwin Race Two Highlights"
        episode_num="8"
    elif [[ $filename == *Review.Show.One* ]]; then
        race_type="Review Show One"
        episode_num="9"
    elif [[ $filename == *Review.Show.Two* ]]; then
        race_type="Review Show Two"
        episode_num="10"
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

# Function to process Women's UEFA Euro files
process_womens_uefa_euro() {
    local file="$1"
    local filename=$(basename "$file")

    # Skip sample files
    if [[ $filename == *sample* ]]; then
        echo "Skipping sample file: $filename"
        ((skipped_count++))
        return 0
    fi

    # Check if it's a Women's UEFA Euro file
    if [[ ! $filename =~ ^Womens\.UEFA\.Euro\. ]]; then
        echo "Not a Women's UEFA Euro file: $filename"
        ((error_count++))
        return 1
    fi

    # Parse the filename: Womens.UEFA.Euro.YYYY.MM.DD.Group.X.Team1.Vs.Team2.quality...
    local year=""
    local month=""
    local day=""
    local stage=""
    local team1=""
    local team2=""
    
    # Extract year, date, and match details
    if [[ $filename =~ ^Womens\.UEFA\.Euro\.([0-9]{4})\.([0-9]{2})\.([0-9]{2})\.(.+?)\.([^.]+)\.Vs\.([^.]+)\.[0-9]+p ]]; then
        year="${BASH_REMATCH[1]}"
        month="${BASH_REMATCH[2]}"
        day="${BASH_REMATCH[3]}"
        stage="${BASH_REMATCH[4]}"
        team1="${BASH_REMATCH[5]}"
        team2="${BASH_REMATCH[6]}"
    else
        echo "Could not parse Women's UEFA Euro filename: $filename"
        ((error_count++))
        return 1
    fi

    # Format team names (replace dots with spaces, capitalize)
    team1="${team1//./ }"
    team2="${team2//./ }"
    
    # Format stage (replace dots with spaces)
    stage="${stage//./ }"
    
    # Create a date string for sorting
    local date_string="${year}-${month}-${day}"
    
    # Determine episode number based on actual match schedule
    # Women's UEFA Euro 2025: 31 total matches (24 group stage + 4 QF + 2 SF + 1 Final)
    local episode_num=""
    local season="$year"
    
    # Group stage matches (Episodes 1-24)
    # Based on UEFA Euro 2025 official schedule
    case "$date_string" in
        "2025-07-02")
            if [[ $stage == *"Group A"* && $team1 == "Iceland" ]]; then
                episode_num="01"
            elif [[ $stage == *"Group A"* && $team1 == "Switzerland" ]]; then
                episode_num="02"
            fi
            ;;
        "2025-07-03")
            if [[ $stage == *"Group B"* && $team1 == "Belgium" ]]; then
                episode_num="03"
            elif [[ $stage == *"Group B"* && $team1 == "Spain" ]]; then
                episode_num="04"
            fi
            ;;
        "2025-07-04")
            if [[ $stage == *"Group C"* && $team1 == "Denmark" ]]; then
                episode_num="05"
            elif [[ $stage == *"Group C"* && $team1 == "Germany" ]]; then
                episode_num="06"
            fi
            ;;
        "2025-07-05")
            if [[ $stage == *"Group D"* && $team1 == "Wales" ]]; then
                episode_num="07"
            elif [[ $stage == *"Group D"* && $team1 == "France" ]]; then
                episode_num="08"
            fi
            ;;
        "2025-07-06")
            if [[ $stage == *"Group A"* && $team1 == "Norway" ]]; then
                episode_num="09"
            elif [[ $stage == *"Group A"* && $team1 == "Switzerland" ]]; then
                episode_num="10"
            fi
            ;;
        "2025-07-07")
            if [[ $stage == *"Group B"* && $team1 == "Spain" ]]; then
                episode_num="11"
            elif [[ $stage == *"Group B"* && $team1 == "Portugal" ]]; then
                episode_num="12"
            fi
            ;;
        "2025-07-08")
            if [[ $stage == *"Group C"* && $team1 == "Germany" ]]; then
                episode_num="13"
            elif [[ $stage == *"Group C"* && $team1 == "Poland" ]]; then
                episode_num="14"
            fi
            ;;
        "2025-07-09")
            if [[ $stage == *"Group D"* && $team1 == "England" ]]; then
                episode_num="15"
            elif [[ $stage == *"Group D"* && $team1 == "France" ]]; then
                episode_num="16"
            fi
            ;;
        "2025-07-10")
            if [[ $stage == *"Group A"* && $team1 == "Finland" ]]; then
                episode_num="17"
            elif [[ $stage == *"Group A"* && $team1 == "Norway" ]]; then
                episode_num="18"
            fi
            ;;
        "2025-07-11")
            if [[ $stage == *"Group B"* && $team1 == "Italy" ]]; then
                episode_num="19"
            elif [[ $stage == *"Group B"* && $team1 == "Portugal" ]]; then
                episode_num="20"
            fi
            ;;
        "2025-07-12")
            if [[ $stage == *"Group C"* && $team1 == "Sweden" ]]; then
                episode_num="21"
            elif [[ $stage == *"Group C"* && $team1 == "Poland" ]]; then
                episode_num="22"
            fi
            ;;
        "2025-07-13")
            if [[ $stage == *"Group D"* && $team1 == "Netherlands" ]]; then
                episode_num="23"
            elif [[ $stage == *"Group D"* && $team1 == "England" ]]; then
                episode_num="24"
            fi
            ;;
        # Knockout stage
        "2025-07-16") episode_num="25" ;;  # QF1
        "2025-07-17") episode_num="26" ;;  # QF3
        "2025-07-18") episode_num="27" ;;  # QF2
        "2025-07-19") episode_num="28" ;;  # QF4
        "2025-07-22") episode_num="29" ;;  # SF1
        "2025-07-23") episode_num="30" ;;  # SF2
        "2025-07-27") episode_num="31" ;;  # Final
        *)
            # Fallback for any unmatched dates - use simple date-based numbering
            local days_since_start=$(( ($(date -d "$date_string" +%s) - $(date -d "2025-07-02" +%s)) / 86400 + 1 ))
            episode_num=$(printf "%02d" $days_since_start)
            ;;
    esac
    
    # Ensure episode number is formatted correctly
    episode_num=$(printf "%02d" $((10#$episode_num)))

    # Create match description
    local match_desc="$stage - $team1 vs $team2"

    # Format the output
    echo "----------------------------------------"
    echo "⚽ Women's UEFA Euro Processing Details:"
    echo "----------------------------------------"
    echo "📅 Year: $year"
    echo "📅 Date: $date_string"
    echo "🏆 Stage: $stage"
    echo "⚽ Match: $team1 vs $team2"
    echo "📺 Episode: S${year}E${episode_num}"
    echo "----------------------------------------"

    # Get file extension
    local extension="${filename##*.}"

    # Create target directories
    local tournament_dir="$DEST_DIR/Women's UEFA Euro $year"
    local season_dir="$tournament_dir/Season $year"
    mkdir -p "$season_dir"

    # Create the target filename
    local target_file="$season_dir/Women's UEFA Euro - S${year}E${episode_num} - ${match_desc}.${extension}"

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
            send_pushover_notification "<b>⚽ Processed Women's UEFA Euro file</b><br><br>Year: ${year}<br>Date: ${date_string}<br>Stage: ${stage}<br>Match: ${team1} vs ${team2}<br>Episode: S${year}E${episode_num}" "Women's UEFA Euro Processing Complete"
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
    
    # Debug output for monitoring loop
    if [ "$DEBUG" = "true" ]; then
        echo "$(date): Checking for new files..."
        echo "DEBUG: Running find command for new MKV and MP4 files..."
    fi
    
    # Check for new files periodically - only files modified in the last interval
    find_output=$(find "$SRC_DIR" \( -type f \( -name "*.mkv" -o -name "*.mp4" \) -o -type d \) -mmin -$((PROCESS_INTERVAL/60+1)) 2>&1)
    find_status=$?
    
    if [ $find_status -ne 0 ]; then
        echo "ERROR: Find command failed with status $find_status: $find_output"
        if [ "$PUSHOVER_NOTIFICATION" = true ]; then
            send_pushover_error_notification "<b>❌ Find Command Failed</b><br><br>Status: $find_status<br>Error: $find_output" "Sports Organizer Error"
        fi
    else
        if [ "$DEBUG" = "true" ]; then
            echo "DEBUG: Find command completed successfully"
        fi
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
        if [ "$DEBUG" = "true" ]; then
            echo "DEBUG: No files processed in this cycle"
        fi
    fi
    
    if [ "$DEBUG" = "true" ]; then
        echo "DEBUG: Sleeping for $PROCESS_INTERVAL seconds..."
    fi
    sleep $PROCESS_INTERVAL
    if [ "$DEBUG" = "true" ]; then
        echo "DEBUG: Woke up from sleep"
    fi
done
