#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from flask import g, request
import flask_restful as restful
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.sql.elements import UnaryExpression
from sqlalchemy.orm.attributes import InstrumentedAttribute
from marshmallow import Schema, fields, post_load
from marshmallow.validate import Length

from silly_blog.app import api, db, auth
from silly_blog.app.models import Tag
from silly_blog.contrib.utils import (envelope_json_required,
                                      make_error_response,
                                      parse_isotime)


LOG = logging.getLogger(__name__)


class CreateTagSchema(Schema):
    """Validate create tag input"""
    name = fields.Str(required=True, validate=Length(min=1, max=64))

    @post_load
    def make_tag(self, data):
        return Tag.from_dict(data)


class UpdateTagSchema(Schema):
    """Validate update tag input"""
    name = fields.Str(validate=Length(min=1, max=64))


@api.resource("/tags/",
              methods=["POST", "GET"],
              endpoint="tags")
@api.resource("/tags/<string:tag_id>",
              methods=["GET", "PUT", "DELETE"],
              endpoint="tag")
class TagResource(restful.Resource):
    """Controller for article tag resources"""

    def __init__(self):
        super().__init__()
        self.post_schema = CreateTagSchema()
        self.put_schema = UpdateTagSchema()

    @staticmethod
    def _get_by_id(tag_id):
        tag = Tag.query.get(tag_id)
        if not tag:
            return make_error_response(404, "Tag %r not found" % tag_id)
        
        return {"tag": tag.to_dict()}

    def get(self, tag_id=None):
        """List tags or show details of a specified one."""
        if tag_id:
            return self._get_by_id(tag_id)

        # refactor
        query = Tag.query

        # filter by name
        name = request.args.get("name")
        if name is not None:
            # regexp maybe not supported, use like instead
            exp = Tag.name.like(''.join(['%', name, '%']))
            query = query.filter(exp)

        # since
        since = request.args.get("since")
        if since is not None:
            try:
                since = parse_isotime(since)
            except ValueError as ex:
                return make_error_response(400, str(ex))
            else:
                query = query.filter(Tag.updated_at >= since)

        # order by related
        sort = request.args.get("sort", "updated_at")
        sort_attribute = getattr(Tag, sort, None)
        if not isinstance(sort_attribute, InstrumentedAttribute):
            return make_error_response(400, "Unknown sort %r" % sort)
        direction = request.args.get("direction", "desc")
        try:
            sort_method = getattr(sort_attribute, direction)
            sort_exp = sort_method()
            if not isinstance(sort_exp, UnaryExpression):
                raise TypeError("Not a unary expression!")
        except (AttributeError, TypeError):
            return make_error_response(400, "Unknown direction %r" % direction)
        else:
            query = query.order_by(sort_exp)

        # before offset and limit, we get the entry total number
        total = query.count()

        # offset limit related
        page = request.args.get("page", None)
        pagesize = request.args.get("pagesize", None)
        if page and pagesize:
            try:
                page = int(page)
            except ValueError:
                return make_error_response(400, "Unknown page %r" % page)
            try:
                pagesize = int(pagesize)
            except ValueError:
                return make_error_response(400, "Unknown pagesize %r" % pagesize)

            offset = (page - 1) * pagesize # page starts from 1
            if offset < 0:
                offset = 0
            query = query.offset(offset).limit(pagesize)

        tags = query.all()
        return {
            "tags": [tag.to_dict() for tag in tags],
            "total": total,
        }

    @auth.login_required
    @envelope_json_required("tag")
    def post(self):
        """Create a article tag.

        Accept tag as a dict that looks like:
            {
                "tag": {
                    "name": "db",
                }
            }
        """
        result = self.post_schema.load(g.tag)
        if result.errors:
            return make_error_response(400, result.errors)

        try:
            tag = result.data
            db.session.add(tag)
            db.session.commit()
        except IntegrityError as ex:
            db.session.rollback()
            reason = str(ex.orig)
            if any(word in reason.lower() for word in ["duplicate", "unique"]):
                return make_error_response(409, reason)
            LOG.exception("An unknown db error occurred")
            return make_error_response(500, "DB Error", ex.code)
        else:
            return {
                "tag": tag.to_dict()
            }

    @auth.login_required
    @envelope_json_required("tag")
    def put(self, tag_id):
        """Update the editable attributes of an exitsing tag.

        :param tag_id: an exitsing tag id.
        """
        result = self.put_schema.load(g.tag)
        if result.errors:
            return make_error_response(400, result.errors)

        tag = Tag.query.get(tag_id)
        if not tag:
            return make_error_response(404, "Tag %r not found" % tag_id)

        try:
            tag.update(**result.data)
        except IntegrityError as ex:
            db.session.rollback()
            reason = str(ex.orig)
            if any(word in reason.lower() for word in ["duplicate", "unique"]):
                return make_error_response(409, reason)
            LOG.exception("An unknown db error occurred")
            return make_error_response(500, "DB Error", ex.code)
        else:
            return {
                "tag": tag.to_dict()
            }

    @auth.login_required
    def delete(self, tag_id):
        """Delete a exitsing tag.

        :param tag_id: an exitsing tag id.
        """
        tag = Tag.query.get(tag_id)
        if not tag:
            return make_error_response(404, "Tag %r not found" % tag_id)

        try:
            tag.delete()
        except DatabaseError as ex:
            LOG.exception("An unknown db error occurred")
            return make_error_response(500, "DB Error", ex.code)
        else:
            return None, 204
