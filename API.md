API Documentation
Agent Routes (agent_routes.py)
1. GET /api/agents

Usage: Retrieve a list of all users with the "agent" role.
Description: Returns a JSON array of serialized agent objects. Requires admin or agent role.
Roles Required: admin, agent

2. POST /api/agents

Usage: Create a new agent user.
Description: Creates a new user with the role "agent" using the provided username and password. Returns the created agent's details. Requires admin role.
Roles Required: admin
Request Body: { "username": string, "password": string }
Response: { "message": "Agent created", "agent": object } (201) or error (400)

3. PUT /api/agents/<int:agent_id>

Usage: Update an existing agent's details.
Description: Updates the specified agent's username, password, or online status. Returns the updated agent's details. Requires admin role.
Roles Required: admin
Request Body: { "username": string, "password": string, "online": boolean }
Response: { "message": "Agent updated", "agent": object } (200) or error (404, 400)

4. DELETE /api/agents/<int:agent_id>

Usage: Delete an existing agent.
Description: Deletes the specified agent and their associated password reset entries. Requires admin role.
Roles Required: admin
Response: { "message": "Agent deleted" } (200) or error (404)

5. GET /api/agents/<int:agent_id>/assignments

Usage: Retrieve all stream assignments for a specific agent.
Description: Returns a JSON array of serialized assignment objects for the specified agent. Requires admin or agent role.
Roles Required: admin, agent
Response: Array of assignment objects (200) or error (404)

6. GET /api/agent/notifications

Usage: Retrieve notifications assigned to the logged-in agent.
Description: Returns a list of notifications from the DetectionLog assigned to the current agent, ordered by timestamp. Requires agent role.
Roles Required: agent
Response: Array of notification objects (200) or error (401, 404, 500)

7. PUT /api/agent/notifications/<int:notification_id>/read

Usage: Mark a specific notification as read for the logged-in agent.
Description: Marks the specified notification as read if it is assigned to the current agent. Requires agent role.
Roles Required: agent
Response: { "message": "Notification marked as read" } (200) or error (401, 404, 403, 500)

8. PUT /api/agent/notifications/read-all

Usage: Mark all notifications assigned to the logged-in agent as read.
Description: Marks all notifications assigned to the current agent as read. Requires agent role.
Roles Required: agent
Response: { "message": "Marked {count} notifications as read" } (200) or error (401, 404, 500)

Assignment Routes (assignment_routes.py)
1. POST /api/assign

Usage: Assign an agent to a stream.
Description: Creates a new assignment linking an agent to a stream. Emits a real-time update. Requires admin role.
Roles Required: admin
Request Body: { "agent_id": int, "stream_id": int }
Response: { "message": "Assignment created successfully", "assignment": object } (201) or error (400, 404, 500)

2. GET /api/assignments

Usage: Retrieve assignments with optional filters.
Description: Returns a list of assignments, optionally filtered by stream_id or agent_id, with eager loading of related agent and stream data. Requires authentication.
Roles Required: Any authenticated user
Query Parameters: stream_id (int), agent_id (int)
Response: { "count": int, "assignments": array } (200)

3. GET /api/assignments/stream/<int:stream_id>

Usage: Retrieve all assignments for a specific stream.
Description: Returns detailed information about assignments for the specified stream, including agent details. Requires authentication.
Roles Required: Any authenticated user
Response: { "stream_id": int, "stream_url": string, "stream_type": string, "assignment_count": int, "assigned_agents": array } (200) or error (404)

4. POST /api/assignments/stream/<int:stream_id>

Usage: Manage assignments for a specific stream.
Description: Replaces existing assignments for the stream with new ones specified in the request. Requires admin role.
Roles Required: admin
Request Body: { "agent_ids": array }
Response: { "message": "Assignments updated successfully", "assigned_agents": array, "assignment_count": int, "assignments": array } (200) or error (404, 500)

5. DELETE /api/assignments/<int:assignment_id>

Usage: Delete a specific assignment.
Description: Removes the specified assignment. Requires admin role.
Roles Required: admin
Response: { "message": "Assignment deleted successfully" } (200) or error (404)

6. GET /api/analytics/agent-performance

Usage: Retrieve performance metrics for the logged-in agent.
Description: Returns mock performance metrics (resolution rate, average response time, detection breakdown) for the current agent. Requires agent role.
Roles Required: agent
Response: { "resolutionRate": int, "avgResponseTime": float, "detectionBreakdown": array, "activityTimeline": array } (200)

Dashboard Routes (dashboard_routes.py)
1. GET /api/dashboard

Usage: Retrieve dashboard data for all streams.
Description: Returns a list of all streams with their assignments and associated agent details. Requires authentication.
Roles Required: Any authenticated user
Response: { "ongoing_streams": int, "streams": array } (200) or error (500)

2. GET /api/agent/dashboard

Usage: Retrieve dashboard data for the logged-in agent.
Description: Returns the count and details of streams assigned to the current agent. Requires authentication.
Roles Required: Any authenticated user
Response: { "ongoing_streams": int, "assignments": array } (200)

Detection Routes (detection_routes.py)
1. GET /detection-images/<filename>

Usage: Serve a detection image file.
Description: Serves an image file from the detections directory. No authentication required.
Response: Image file or error

2. POST /api/detect

Usage: Perform unified detection on text or visual frame.
Description: Processes text or visual frame data for chat or visual detection, returning results. No authentication required.
Request Body: { "text": string, "visual_frame": array }
Response: { "chat": array, "visual": array } (200)

3. POST /api/livestream

Usage: Retrieve a livestream URL from an M3U8 URL.
Description: Fetches and parses an M3U8 URL to return the stream URL. No authentication required.
Request Body: { "url": string }
Response: { "stream_url": string } (200) or error (400, 500)

4. POST /api/trigger-detection

Usage: Start or stop detection for a stream.
Description: Starts or stops monitoring for the specified stream. Requires admin or agent role.
Roles Required: admin, agent
Request Body: { "stream_id": int, "stop": boolean }
Response: { "message": string, "stream_id": int, "active": boolean, ... } (200, 409) or error (400, 401, 404, 500)

5. GET /api/detection-status/<int:stream_id>

Usage: Check the detection status of a stream.
Description: Returns the current detection status for the specified stream. Requires authentication.
Roles Required: Any authenticated user
Response: { "stream_id": int, "stream_url": string, "active": boolean, ... } (200) or error (404)

Health Routes (health_routes.py)
1. GET /health

Usage: Check the health status of the application.
Description: Returns a simple "OK" response to indicate the server is running. No authentication required.
Response: "OK" (200)

Keyword/Object Routes (keyword_object_routes.py)
1. GET /api/keywords

Usage: Retrieve all chat keywords.
Description: Returns a list of all chat keywords. Requires admin or agent role.
Roles Required: admin, agent
Response: Array of keyword objects (200)

2. POST /api/keywords

Usage: Create a new chat keyword.
Description: Adds a new keyword to the database and refreshes flagged keywords. Requires admin role.
Roles Required: admin
Request Body: { "keyword": string }
Response: { "message": "Keyword added", "keyword": object } (201) or error (400)

3. PUT /api/keywords/<int:keyword_id>

Usage: Update an existing chat keyword.
Description: Updates the specified keyword and refreshes flagged keywords. Requires admin role.
Roles Required: admin
Request Body: { "keyword": string }
Response: { "message": "Keyword updated", "keyword": object } (200) or error (404, 400)

4. DELETE /api/keywords/<int:keyword_id>

Usage: Delete a chat keyword.
Description: Removes the specified keyword and refreshes flagged keywords. Requires admin role.
Roles Required: admin
Response: { "message": "Keyword deleted" } (200) or error (404)

5. GET /api/objects

Usage: Retrieve all flagged objects.
Description: Returns a list of all flagged objects. Requires admin or agent role.
Roles Required: admin, agent
Response: Array of object objects (200)

6. POST /api/objects

Usage: Create a new flagged object.
Description: Adds a new flagged object to the database. Requires admin role.
Roles Required: admin
Request Body: { "object_name": string }
Response: { "message": "Object added", "object": object } (201) or error (400)

7. PUT /api/objects/<int:object_id>

Usage: Update an existing flagged object.
Description: Updates the specified flagged object. Requires admin role.
Roles Required: admin
Request Body: { "object_name": string }
Response: { "message": "Object updated", "object": object } (200) or error (404, 400)

8. DELETE /api/objects/<int:object_id>

Usage: Delete a flagged object.
Description: Removes the specified flagged object. Requires admin role.
Roles Required: admin
Response: { "message": "Object deleted" } (200) or error (404)

Authentication Routes (auth_routes.py)
1. POST /api/login

Usage: Authenticate a user.
Description: Logs in a user with username/email and password, setting session cookies. Returns user details.
Request Body: { "username": string, "password": string }
Response: { "message": "Login successful", "role": string, ... } (200) or error (400, 401, 500)

2. POST /api/logout

Usage: Log out the current user.
Description: Clears the session and removes session cookies.
Response: { "message": "Logged out successfully" } (200)

3. GET /api/session

Usage: Check the current session status.
Description: Returns whether the user is logged in and their details if authenticated.
Response: { "isLoggedIn": boolean, "user": object } (200) or { "isLoggedIn": false } (200)

4. POST /api/check-username

Usage: Check if a username is available.
Description: Validates the username format and checks for availability.
Request Body: { "username": string }
Response: { "available": boolean, "message": string } (200, 400)

5. POST /api/check-email

Usage: Check if an email is available.
Description: Validates the email format and checks for availability.
Request Body: { "email": string }
Response: { "available": boolean, "message": string } (200, 400)

6. POST /api/register

Usage: Register a new user.
Description: Creates a new user account with the provided details, logs them in, and sends a welcome email.
Request Body: { "username": string, "email": string, "password": string, "receiveUpdates": boolean, "telegram_username": string, "telegram_chat_id": string }
Response: { "message": "Account created successfully", "user": object } (201) or error (400, 500)

7. POST /api/forgot-password

Usage: Initiate a password reset.
Description: Generates a reset token and sends a password reset email if the email is registered.
Request Body: { "email": string }
Response: { "message": "If your email is registered, you will receive a password reset code" } (200) or error (400, 500)

8. POST /api/verify-reset-token

Usage: Verify a password reset token.
Description: Checks if the provided token is valid and not expired.
Request Body: { "token": string }
Response: { "valid": boolean, "message": string } (200, 400)

9. POST /api/reset-password

Usage: Reset a user's password.
Description: Resets the password using a valid reset token and sends a confirmation email.
Request Body: { "token": string, "password": string }
Response: { "message": "Password has been reset successfully..." } (200) or error (400, 404, 500)

10. POST /api/change-password

Usage: Change the current user's password.
Description: Updates the password after verifying the current password and sends a confirmation email. Requires authentication.
Roles Required: Any authenticated user
Request Body: { "currentPassword": string, "newPassword": string }
Response: { "message": "Password changed successfully" } (200) or error (400, 404, 500)

11. POST /api/update-profile

Usage: Update the current user's profile.
Description: Updates user profile fields like name, bio, or Telegram details. Requires authentication.
Roles Required: Any authenticated user
Request Body: { "name": string, "bio": string, "receive_updates": boolean, "telegram_username": string, "telegram_chat_id": string }
Response: { "message": "Profile updated successfully", "user": object } (200) or error (400, 404, 500)

12. GET /api/user/telegram

Usage: Retrieve the current user's Telegram details.
Description: Returns the user's Telegram username, chat ID, and update preference. Requires authentication.
Roles Required: Any authenticated user
Response: { "telegram_username": string, "chat_id": string, "receive_updates": boolean } (200) or error (404)

13. POST /api/user/telegram

Usage: Update the current user's Telegram details.
Description: Updates the user's Telegram username, chat ID, and update preference. Requires authentication.
Roles Required: Any authenticated user
Request Body: { "telegram_username": string, "chat_id": string, "receive_updates": boolean }
Response: `{ "message": "Telegram details Ascendingly sorts by relevance, then by date.

