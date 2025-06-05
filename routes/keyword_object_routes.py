from flask import Blueprint, request, jsonify
from extensions import db
from models import ChatKeyword, FlaggedObject, User
from utils import login_required
from flask_jwt_extended import get_jwt_identity

keyword_bp = Blueprint('keyword', __name__)

@keyword_bp.route("/api/keywords", methods=["GET"])

def get_keywords():
    keywords = ChatKeyword.query.all()
    return jsonify([kw.serialize() for kw in keywords])

@keyword_bp.route("/api/keywords", methods=["POST"])

def create_keyword():
    data = request.get_json()
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"message": "Keyword required"}), 400
    if ChatKeyword.query.filter_by(keyword=keyword).first():
        return jsonify({"message": "Keyword exists"}), 400
    kw = ChatKeyword(keyword=keyword)
    db.session.add(kw)
    db.session.commit()
    
    from monitoring import refresh_flagged_keywords
    refresh_flagged_keywords()
    
    return jsonify({"message": "Keyword added", "keyword": kw.serialize()}), 201

@keyword_bp.route("/api/keywords/<int:keyword_id>", methods=["PUT"])

def update_keyword(keyword_id):
    kw = ChatKeyword.query.get(keyword_id)
    if not kw:
        return jsonify({"message": "Keyword not found"}), 404
    data = request.get_json()
    new_kw = data.get("keyword", "").strip()
    if not new_kw:
        return jsonify({"message": "New keyword required"}), 400
    kw.keyword = new_kw
    db.session.commit()
    
    from monitoring import refresh_flagged_keywords
    refresh_flagged_keywords()
    
    return jsonify({"message": "Keyword updated", "keyword": kw.serialize()})

@keyword_bp.route("/api/keywords/<int:keyword_id>", methods=["DELETE"])

def delete_keyword(keyword_id):
    kw = ChatKeyword.query.get(keyword_id)
    if not kw:
        return jsonify({"message": "Keyword not found"}), 404
    db.session.delete(kw)
    db.session.commit()
    
    from monitoring import refresh_flagged_keywords
    refresh_flagged_keywords()
    
    return jsonify({"message": "Keyword deleted"})

@keyword_bp.route("/api/objects", methods=["GET"])

def get_objects():
    objects = FlaggedObject.query.all()
    return jsonify([obj.serialize() for obj in objects])

@keyword_bp.route("/api/objects", methods=["POST"])

def create_object():
    data = request.get_json()
    obj_name = data.get("object_name", "").strip()
    if not obj_name:
        return jsonify({"message": "Object name required"}), 400
    if FlaggedObject.query.filter_by(object_name=obj_name).first():
        return jsonify({"message": "Object exists"}), 400
    obj = FlaggedObject(object_name=obj_name)
    db.session.add(obj)
    db.session.commit()
    return jsonify({"message": "Object added", "object": obj.serialize()}), 201

@keyword_bp.route("/api/objects/<int:object_id>", methods=["PUT"])

def update_object(object_id):
    obj = FlaggedObject.query.get(object_id)
    if not obj:
        return jsonify({"message": "Object not found"}), 404
    data = request.get_json()
    new_name = data.get("object_name", "").strip()
    if not new_name:
        return jsonify({"message": "New name required"}), 400
    obj.object_name = new_name
    db.session.commit()
    return jsonify({"message": "Object updated", "object": obj.serialize()})

@keyword_bp.route("/api/objects/<int:object_id>", methods=["DELETE"])

def delete_object(object_id):
    obj = FlaggedObject.query.get(object_id)
    if not obj:
        return jsonify({"message": "Object not found"}), 404
    db.session.delete(obj)
    db.session.commit()
    return jsonify({"message": "Object deleted"})