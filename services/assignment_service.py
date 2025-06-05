# services/assignment_service.py
from extensions import db
from models import Assignment, User, Stream
from services.notification_service import NotificationService
import logging

class AssignmentService:
    @staticmethod
    def assign_stream_to_agent(
        stream_id, agent_id, assigner_id=None, notes=None, priority='normal', metadata=None
    ):
        """Assign a stream to an agent with comprehensive error handling."""
        try:
            # Validate inputs
            stream = Stream.query.get(stream_id)
            agent = User.query.filter_by(id=agent_id, role='agent').first()
            assigner = User.query.get(assigner_id) if assigner_id else None

            if not stream:
                raise ValueError("Stream not found")
            if not agent:
                raise ValueError("Agent not found")

            # Check for existing assignment to avoid duplicates
            existing = Assignment.query.filter_by(
                stream_id=stream_id, agent_id=agent_id, status='active'
            ).first()
            if existing:
                return existing, False  # Assignment exists, not created

            # Create new assignment
            assignment = Assignment(
                stream_id=stream_id,
                agent_id=agent_id,
                assigned_by=assigner_id,
                notes=notes,
                priority=priority,
                status='active',
                metadata=metadata or {},
            )
            db.session.add(assignment)
            db.session.commit()

            # Notify agent and admins
            NotificationService.notify_assignment(agent, stream, assigner, notes, priority)
            NotificationService.notify_admins(
                'stream_assigned',
                {
                    'message': f"Stream {stream.streamer_username} assigned to {agent.username}",
                    'room_url': stream.room_url,
                    'streamer_username': stream.streamer_username,
                    'agent_username': agent.username,
                    'priority': priority,
                },
                stream.room_url,
                stream.type,
                stream.streamer_username,
            )

            return assignment, True  # Assignment created
        except Exception as e:
            db.session.rollback()
            logging.error(f"Failed to assign stream {stream_id} to agent {agent_id}: {str(e)}")
            raise

    @staticmethod
    def auto_assign_stream(stream_id, assigner_id=None):
        """Automatically assign a stream to an agent with the least workload."""
        try:
            stream = Stream.query.get(stream_id)
            if not stream:
                raise ValueError("Stream not found")

            # Find agent with least assignments, prioritizing online agents
            agents = User.query.filter_by(role='agent').all()
            if not agents:
                logging.info(f"No agents available for stream {stream_id}")
                return None, False

            agent_workload = {}
            for agent in agents:
                assignment_count = Assignment.query.filter_by(
                    agent_id=agent.id, status='active'
                ).count()
                agent_workload[agent.id] = {
                    "count": assignment_count,
                    "online": agent.online,
                }

            # Select agent
            selected_agent_id = None
            min_assignments = float('inf')
            for agent_id, workload in agent_workload.items():
                if workload["online"] and workload["count"] < min_assignments:
                    selected_agent_id = agent_id
                    min_assignments = workload["count"]
            if not selected_agent_id:
                for agent_id, workload in agent_workload.items():
                    if workload["count"] < min_assignments:
                        selected_agent_id = agent_id
                        min_assignments = workload["count"]

            if selected_agent_id:
                return AssignmentService.assign_stream_to_agent(
                    stream_id=stream_id,
                    agent_id=selected_agent_id,
                    assigner_id=assigner_id,
                    notes="Auto-assigned based on workload",
                    metadata={"auto_assigned": True},
                )
            return None, False
        except Exception as e:
            logging.error(f"Failed to auto-assign stream {stream_id}: {str(e)}")
            db.session.rollback()
            raise

    @staticmethod
    def update_assignment(assignment_id, updates, assigner_id=None):
        """Update an existing assignment."""
        try:
            assignment = Assignment.query.get(assignment_id)
            if not assignment:
                raise ValueError("Assignment not found")

            allowed_updates = ['notes', 'priority', 'status', 'metadata']
            for key, value in updates.items():
                if key in allowed_updates:
                    setattr(assignment, key, value)

            if assigner_id:
                assignment.assigned_by = assigner_id

            db.session.commit()

            # Notify agent and admins
            NotificationService.notify_assignment(
                assignment.agent,
                assignment.stream,
                User.query.get(assigner_id) if assigner_id else None,
                assignment.notes,
                assignment.priority,
            )
            NotificationService.notify_admins(
                'assignment_updated',
                {
                    'message': f"Assignment for {assignment.stream.streamer_username} updated",
                    'room_url': assignment.stream.room_url,
                    'streamer_username': assignment.stream.streamer_username,
                    'agent_username': assignment.agent.username,
                    'updates': updates,
                },
                assignment.stream.room_url,
                assignment.stream.type,
                assignment.stream.streamer_username,
            )

            return assignment
        except Exception as e:
            db.session.rollback()
            logging.error(f"Failed to update assignment {assignment_id}: {str(e)}")
            raise