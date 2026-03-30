#!/bin/bash

# Flat Manager - Unified Service Management Script
# Combines start, stop, restart, status, and setup functionality

set -e

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Service names and PID files
declare -A SERVICES=(
    ["django"]="logs/django.pid"
    ["celery"]="logs/celery.pid"
    ["celery-ops"]="logs/celery-ops.pid"
    ["beat"]="logs/celery-beat.pid"
    ["daphne"]="logs/daphne.pid"
)

# Function to check if a service is running
is_running() {
    local pidfile=$1
    if [ -f "$pidfile" ]; then
        local pid=$(cat "$pidfile")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Function to stop a service
stop_service() {
    local name=$1
    local pidfile=$2
    
    if [ -f "$pidfile" ]; then
        local pid=$(cat "$pidfile")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${YELLOW}🛑 Stopping $name (PID: $pid)...${NC}"
            kill "$pid" 2>/dev/null || true
            sleep 2
            
            # Force kill if still running
            if ps -p "$pid" > /dev/null 2>&1; then
                echo -e "${YELLOW}⚠️  Force killing $name...${NC}"
                kill -9 "$pid" 2>/dev/null || true
            fi
            
            echo -e "${GREEN}✅ $name stopped${NC}"
        else
            echo -e "${YELLOW}⚠️  $name is not running (stale PID file)${NC}"
        fi
        rm -f "$pidfile"
    else
        echo -e "${YELLOW}⚠️  No PID file found for $name${NC}"
    fi
}

# Function to start services
start_services() {
    echo -e "${BLUE}🚀 Starting Flat Manager services...${NC}"
    echo ""
    
    # Load environment variables
    if [ -f .env ]; then
        export $(cat .env | grep -v '^#' | xargs)
    else
        echo -e "${YELLOW}⚠️  No .env file found. Using defaults.${NC}"
    fi
    
    # Create necessary directories
    mkdir -p logs repos builds media staticfiles
    
    # Activate virtual environment if it exists
    if [ -d "venv" ]; then
        source venv/bin/activate
    fi
    
    # Check if Redis is running
    echo -e "${BLUE}📡 Checking Redis/Valkey...${NC}"
    if ! redis-cli ping > /dev/null 2>&1; then
        echo -e "${RED}⚠️  Redis/Valkey is not running. Please start it first.${NC}"
        echo "   Run: redis-server &"
        exit 1
    fi
    echo -e "${GREEN}✅ Redis/Valkey is running${NC}"
    echo ""
    
    # Start Django development server
    echo -e "${BLUE}🌐 Starting Django server...${NC}"
    if is_running "${SERVICES[django]}"; then
        echo -e "${YELLOW}⚠️  Django server is already running (PID: $(cat ${SERVICES[django]}))${NC}"
    else
        python manage.py migrate --noinput
        python manage.py collectstatic --noinput --clear > /dev/null 2>&1
        nohup python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &
        echo $! > ${SERVICES[django]}
        echo -e "${GREEN}✅ Django server started (PID: $!)${NC}"
    fi
    
    # Start Celery build worker
    echo -e "${BLUE}⚙️  Starting Celery build worker...${NC}"
    if is_running "${SERVICES[celery]}"; then
        echo -e "${YELLOW}⚠️  Celery build worker is already running (PID: $(cat ${SERVICES[celery]}))${NC}"
    else
        FLAT_MANAGER_WORKER_TYPE=build nohup celery -A config worker --queues=build --hostname=build@%h --loglevel=info > logs/celery.log 2>&1 &
        echo $! > ${SERVICES[celery]}
        echo -e "${GREEN}✅ Celery build worker started (PID: $!)${NC}"
    fi

    # Start Celery ops worker
    echo -e "${BLUE}⚙️  Starting Celery ops worker...${NC}"
    if is_running "${SERVICES[celery-ops]}"; then
        echo -e "${YELLOW}⚠️  Celery ops worker is already running (PID: $(cat ${SERVICES[celery-ops]}))${NC}"
    else
        OPS_CONCURRENCY=${CELERY_OPS_WORKER_CONCURRENCY:-4}
        nohup celery -A config worker --queues=ops --concurrency=${OPS_CONCURRENCY} --hostname=ops@%h --loglevel=info > logs/celery-ops.log 2>&1 &
        echo $! > ${SERVICES[celery-ops]}
        echo -e "${GREEN}✅ Celery ops worker started (PID: $!, concurrency: ${OPS_CONCURRENCY})${NC}"
    fi
    
    # Start Celery beat (scheduler)
    echo -e "${BLUE}⏰ Starting Celery beat...${NC}"
    if is_running "${SERVICES[beat]}"; then
        echo -e "${YELLOW}⚠️  Celery beat is already running (PID: $(cat ${SERVICES[beat]}))${NC}"
    else
        nohup celery -A config beat --loglevel=info > logs/celery-beat.log 2>&1 &
        echo $! > ${SERVICES[beat]}
        echo -e "${GREEN}✅ Celery beat started (PID: $!)${NC}"
    fi
    
    # Start Daphne (ASGI server for WebSockets)
    echo -e "${BLUE}🔌 Starting Daphne (WebSocket server)...${NC}"
    if is_running "${SERVICES[daphne]}"; then
        echo -e "${YELLOW}⚠️  Daphne is already running (PID: $(cat ${SERVICES[daphne]}))${NC}"
    else
        nohup daphne -b 0.0.0.0 -p 8001 config.asgi:application > logs/daphne.log 2>&1 &
        echo $! > ${SERVICES[daphne]}
        echo -e "${GREEN}✅ Daphne started (PID: $!)${NC}"
    fi
    
    echo ""
    echo -e "${GREEN}🎉 All services started successfully!${NC}"
    echo ""
    echo "Services running:"
    echo "  - Django:              http://localhost:8000"
    echo "  - WebSockets:          ws://localhost:8001"
    echo "  - Celery Build Worker: $(cat ${SERVICES[celery]} 2>/dev/null || echo 'Not running')"
    echo "  - Celery Ops Worker:   $(cat ${SERVICES[celery-ops]} 2>/dev/null || echo 'Not running')"
    echo "  - Celery Beat:         $(cat ${SERVICES[beat]} 2>/dev/null || echo 'Not running')"
    echo ""
    echo "To view logs:"
    echo "  - Django:              tail -f logs/django.log"
    echo "  - Celery Build Worker: tail -f logs/celery.log"
    echo "  - Celery Ops Worker:   tail -f logs/celery-ops.log"
    echo "  - Celery Beat:         tail -f logs/celery-beat.log"
    echo "  - Daphne:              tail -f logs/daphne.log"
    echo ""
}

# Function to stop services
stop_services() {
    echo -e "${BLUE}🛑 Stopping Flat Manager services...${NC}"
    echo ""
    
    stop_service "Daphne" "${SERVICES[daphne]}"
    stop_service "Celery Beat" "${SERVICES[beat]}"
    stop_service "Celery Ops Worker" "${SERVICES[celery-ops]}"
    stop_service "Celery Build Worker" "${SERVICES[celery]}"
    stop_service "Django" "${SERVICES[django]}"
    
    echo ""
    echo -e "${GREEN}✅ All services stopped successfully!${NC}"
}

# Function to show status
show_status() {
    echo -e "${BLUE}📊 Flat Manager Service Status${NC}"
    echo "================================"
    echo ""
    
    # Check Redis
    echo "Redis/Valkey:"
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Redis/Valkey is running${NC}"
    else
        echo -e "${RED}❌ Redis/Valkey is not running${NC}"
    fi
    echo ""
    
    # Check all services
    echo "Services:"
    for service in django celery celery-ops beat daphne; do
        local name=""
        case $service in
            django) name="Django Server" ;;
            celery) name="Celery Build Worker" ;;
            celery-ops) name="Celery Ops Worker" ;;
            beat) name="Celery Beat" ;;
            daphne) name="Daphne (WebSocket)" ;;
        esac
        
        local pidfile=${SERVICES[$service]}
        if [ -f "$pidfile" ]; then
            local pid=$(cat "$pidfile")
            if ps -p "$pid" > /dev/null 2>&1; then
                echo -e "${GREEN}✅ $name is running (PID: $pid)${NC}"
            else
                echo -e "${RED}❌ $name is not running (stale PID file)${NC}"
            fi
        else
            echo -e "${RED}❌ $name is not running${NC}"
        fi
    done
    
    echo ""
    echo "================================"
}

# Function to restart services
restart_services() {
    echo -e "${BLUE}🔄 Restarting Flat Manager services...${NC}"
    echo ""
    
    stop_services
    sleep 2
    start_services
    
    echo ""
    echo -e "${GREEN}✅ All services restarted successfully!${NC}"
}

# Function to run setup
run_setup() {
    echo -e "${BLUE}🚀 Flat Manager - Initial Setup${NC}"
    echo "================================"
    echo ""
    
    # Check Python version
    echo "📋 Checking Python version..."
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    echo "   Python version: $python_version"
    echo ""
    
    # Check if virtual environment exists
    if [ ! -d "venv" ]; then
        echo -e "${BLUE}🔧 Creating virtual environment...${NC}"
        python3 -m venv venv
        echo -e "${GREEN}✅ Virtual environment created${NC}"
    else
        echo -e "${GREEN}✅ Virtual environment already exists${NC}"
    fi
    echo ""
    
    # Activate virtual environment
    echo -e "${BLUE}🔄 Activating virtual environment...${NC}"
    source venv/bin/activate
    echo ""
    
    # Install dependencies
    echo -e "${BLUE}📦 Installing Python dependencies...${NC}"
    pip install --upgrade pip
    pip install -r requirements.txt
    # Patch bst-plugins-experimental for dulwich 1.x compatibility
    # dulwich 1.x removed ANNOTATED_TAG_SUFFIX from dulwich.refs; shim it in
    GIT_UTILS=$(venv/bin/python -c "import bst_plugins_experimental.sources._git_utils as m; import inspect; print(inspect.getfile(m))" 2>/dev/null || true)
    if [ -n "$GIT_UTILS" ] && grep -q "^from dulwich.refs import ANNOTATED_TAG_SUFFIX" "$GIT_UTILS" 2>/dev/null; then
        sed -i 's/^from dulwich\.refs import ANNOTATED_TAG_SUFFIX/try:\n    from dulwich.refs import ANNOTATED_TAG_SUFFIX\nexcept ImportError:\n    # dulwich 1.x removed this constant; value is the git peeled-ref suffix\n    ANNOTATED_TAG_SUFFIX = b"^{}"/' "$GIT_UTILS"
        echo -e "${GREEN}✅ Applied dulwich 1.x compatibility patch to bst-plugins-experimental${NC}"
    else
        echo -e "${GREEN}✅ dulwich compatibility patch already applied or not needed${NC}"
    fi
    echo ""
    
    # Copy .env file if it doesn't exist
    if [ ! -f ".env" ]; then
        echo -e "${BLUE}📝 Creating .env file from .env.example...${NC}"
        cp .env.example .env
        echo -e "${GREEN}✅ .env file created - please edit with your configuration${NC}"
    else
        echo -e "${GREEN}✅ .env file already exists${NC}"
    fi
    echo ""
    
    # Create necessary directories
    echo -e "${BLUE}📁 Creating necessary directories...${NC}"
    mkdir -p logs repos builds media staticfiles static
    echo -e "${GREEN}✅ Directories created${NC}"
    echo ""
    
    # Check if Redis is installed
    echo -e "${BLUE}📡 Checking Redis/Valkey...${NC}"
    if command -v redis-server &> /dev/null; then
        echo -e "${GREEN}✅ Redis is installed${NC}"
        
        # Check if Redis is running
        if redis-cli ping > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Redis is running${NC}"
        else
            echo -e "${YELLOW}⚠️  Redis is not running${NC}"
            echo "   Please start Redis with: redis-server &"
        fi
    else
        echo -e "${YELLOW}⚠️  Redis is not installed${NC}"
        echo "   Please install Redis:"
        echo "   - Ubuntu/Debian: sudo apt install redis-server"
        echo "   - Fedora: sudo dnf install redis"
        echo "   - Arch: sudo pacman -S redis"
    fi
    echo ""
    
    # Run migrations
    echo -e "${BLUE}🗄️  Running database migrations...${NC}"
    python manage.py migrate
    echo ""
    
    # Collect static files
    echo -e "${BLUE}📦 Collecting static files...${NC}"
    python manage.py collectstatic --noinput --clear
    echo ""
    
    # Create superuser prompt
    echo ""
    echo -e "${BLUE}👤 Create a superuser account${NC}"
    echo "   (Press Ctrl+C to skip)"
    echo ""
    python manage.py createsuperuser || echo "Skipped superuser creation"
    
    echo ""
    echo -e "${GREEN}✅ Setup complete!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Make sure Redis is running: redis-server &"
    echo "  2. Start all services: $0 start"
    echo "  3. Open http://localhost:8000 in your browser"
    echo "  4. Login with the superuser credentials you created"
    echo ""
    echo "Service management:"
    echo "  - Start:   $0 start"
    echo "  - Stop:    $0 stop"
    echo "  - Restart: $0 restart"
    echo "  - Status:  $0 status"
    echo ""
}

# Function to show usage
show_usage() {
    echo "Usage: $0 {start|stop|restart|status|setup}"
    echo ""
    echo "Commands:"
    echo "  start   - Start all services (Django, Celery Worker, Celery Beat, Daphne)"
    echo "  stop    - Stop all services"
    echo "  restart - Restart all services"
    echo "  status  - Show status of all services"
    echo "  setup   - Run initial setup (create venv, install deps, migrations, etc.)"
    echo ""
    echo "Examples:"
    echo "  $0 start        # Start all services"
    echo "  $0 stop         # Stop all services"
    echo "  $0 restart      # Restart all services"
    echo "  $0 status       # Check service status"
    echo "  $0 setup        # Run initial setup"
    echo ""
}

# Main command handler
case "${1:-}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    status)
        show_status
        ;;
    setup)
        run_setup
        ;;
    *)
        show_usage
        exit 1
        ;;
esac
