#!/usr/bin/env python3
"""
User Management CLI Tool

Usage:
    python manage_users.py create <username> <password> [--role admin|user]
    python manage_users.py list
    python manage_users.py change-password <username> <new_password>
    python manage_users.py delete <username>
    python manage_users.py show <username>

Examples:
    # Create admin user
    python manage_users.py create john admin123 --role admin
    
    # Create regular user
    python manage_users.py create jane password456
    
    # List all users
    python manage_users.py list
    
    # Change password
    python manage_users.py change-password john newpassword123
    
    # Delete user
    python manage_users.py delete john
    
    # Show user details
    python manage_users.py show john
"""

import sys
import argparse
from auth import UserManager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='User Management Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Create user command
    create_parser = subparsers.add_parser('create', help='Create a new user')
    create_parser.add_argument('username', help='Username')
    create_parser.add_argument('password', help='Password')
    create_parser.add_argument('--role', choices=['admin', 'staff', 'user'], default='staff',
                             help='User role (default: staff)')
    
    # List users command
    list_parser = subparsers.add_parser('list', help='List all users')
    
    # Change password command
    password_parser = subparsers.add_parser('change-password', help='Change user password')
    password_parser.add_argument('username', help='Username')
    password_parser.add_argument('new_password', help='New password')
    
    # Delete user command
    delete_parser = subparsers.add_parser('delete', help='Delete a user')
    delete_parser.add_argument('username', help='Username')
    
    # Show user command
    show_parser = subparsers.add_parser('show', help='Show user details')
    show_parser.add_argument('username', help='Username')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialize user manager
    try:
        user_mgr = UserManager()
    except Exception as e:
        print(f"❌ Failed to initialize user manager: {e}")
        sys.exit(1)
    
    try:
        # Execute command
        if args.command == 'create':
            create_user(user_mgr, args.username, args.password, args.role)
        
        elif args.command == 'list':
            list_users(user_mgr)
        
        elif args.command == 'change-password':
            change_password(user_mgr, args.username, args.new_password)
        
        elif args.command == 'delete':
            delete_user(user_mgr, args.username)
        
        elif args.command == 'show':
            show_user(user_mgr, args.username)
    
    finally:
        user_mgr.cleanup()


def create_user(user_mgr, username, password, role):
    """Create a new user"""
    print(f"Creating user '{username}' with role '{role}'...")
    
    if user_mgr.create_user(username, password, role):
        print(f"✅ User '{username}' created successfully")
        print(f"   Username: {username}")
        print(f"   Role: {role}")
    else:
        print(f"❌ Failed to create user '{username}'")
        print("   Possible reasons:")
        print("   - Username already exists")
        print("   - Invalid role")
        print("   - Database error")
        sys.exit(1)


def list_users(user_mgr):
    """List all users"""
    users = user_mgr.list_users()
    
    if not users:
        print("No users found")
        return
    
    print(f"\n{'='*80}")
    print(f"{'ID':<5} {'Username':<20} {'Role':<10} {'Active':<10} {'Last Login':<25}")
    print(f"{'='*80}")
    
    for user in users:
        user_id = user['id']
        username = user['username']
        role = user['role']
        is_active = '✓' if user['is_active'] else '✗'
        last_login = user['last_login'] or 'Never'
        
        if user['last_login']:
            # Format timestamp
            from datetime import datetime
            dt = datetime.fromisoformat(user['last_login'])
            last_login = dt.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"{user_id:<5} {username:<20} {role:<10} {is_active:<10} {last_login:<25}")
    
    print(f"{'='*80}\n")
    print(f"Total users: {len(users)}")


def change_password(user_mgr, username, new_password):
    """Change user password"""
    print(f"Changing password for user '{username}'...")
    
    if user_mgr.update_password(username, new_password):
        print(f"✅ Password changed successfully for user '{username}'")
    else:
        print(f"❌ Failed to change password for user '{username}'")
        print("   Possible reasons:")
        print("   - User does not exist")
        print("   - Database error")
        sys.exit(1)


def delete_user(user_mgr, username):
    """Delete (disable) a user"""
    print(f"Deleting user '{username}'...")
    
    # Confirm deletion
    response = input(f"Are you sure you want to delete user '{username}'? (yes/no): ")
    
    if response.lower() != 'yes':
        print("Deletion cancelled")
        return
    
    if user_mgr.delete_user(username):
        print(f"✅ User '{username}' deleted successfully")
    else:
        print(f"❌ Failed to delete user '{username}'")
        print("   Possible reasons:")
        print("   - User does not exist")
        print("   - Database error")
        sys.exit(1)


def show_user(user_mgr, username):
    """Show user details"""
    user = user_mgr.get_user(username)
    
    if not user:
        print(f"❌ User '{username}' not found")
        sys.exit(1)
    
    print(f"\n{'='*50}")
    print(f"User Details: {username}")
    print(f"{'='*50}")
    print(f"ID:          {user['id']}")
    print(f"Username:    {user['username']}")
    print(f"Role:        {user['role']}")
    print(f"Active:      {'Yes' if user['is_active'] else 'No'}")
    print(f"Created:     {user['created_at'] or 'Unknown'}")
    print(f"Last Login:  {user['last_login'] or 'Never'}")
    print(f"{'='*50}\n")


if __name__ == '__main__':
    main()
