#########################################################################
#
# Copyright (C) 2016 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################

""" Django signals connections and associated receiver functions for geonode's
    third-party 'social' apps which include announcements, notifications,
    relationships, actstream user_messages and potentially others
"""
import logging
from collections import defaultdict

from django.conf import settings
from django.db.models import signals
from django.utils.translation import gettext_lazy as _

from geonode.geoapps.models import GeoApp
from geonode.layers.models import Dataset
from geonode.maps.models import Map
from geonode.documents.models import Document
from geonode.notifications_helper import (
    send_notification,
    queue_notification,
    get_notification_recipients,
)

logger = logging.getLogger(__name__)

activity = None
if "actstream" in settings.INSTALLED_APPS:
    from actstream import action as activity
    from actstream.actions import follow, unfollow


def activity_post_modify_object(sender, instance, created=None, **kwargs):
    """
    Creates new activities after a Map, Dataset, or Document is created/updated/deleted.
    """
    verb = None
    obj_type = instance.__class__._meta.object_name.lower()
    action_settings = defaultdict(
        lambda: dict(
            actor=getattr(instance, "owner", None),
            action_object=instance,
            created_verb=_("created"),
            deleted_verb=_("deleted"),
            obj_type=obj_type,
            object_name=getattr(instance, "name", None),
            target=None,
            updated_verb=_("updated"),
        )
    )

    try:
        action_settings["map"].update(
            object_name=getattr(instance, "title", None),
        )
    except Exception as e:
        logger.exception(e)

    try:
        action_settings["dataset"].update(created_verb=_("uploaded"))
    except Exception as e:
        logger.exception(e)

    try:
        action_settings["document"].update(created_verb=_("uploaded"))
    except Exception as e:
        logger.exception(e)

    if obj_type == "dataset" and created:
        # Aqui você adiciona a lógica para perguntar ao usuário sobre a conversão
        if user_wants_conversion():
            # Se o usuário desejar a conversão, converta para EPSG:4764
            convert_to_epsg(instance, 'EPSG:4764')
            move_to_workspace(instance, 'INDE')

    if obj_type not in ["document", "dataset", "map"]:
        try:
            action_settings[obj_type].update(
                object_name=getattr(instance, "title", None),
            )
        except Exception as e:
            logger.exception(e)

    try:
        action = action_settings[obj_type]
        if created:
            # object was created
            verb = action.get("created_verb")
            raw_action = "created"
        else:
            if created is False:
                # object was saved.
                if (
                    not isinstance(instance, Dataset)
                    and not isinstance(instance, Document)
                    and not isinstance(instance, Map)
                    and not isinstance(instance, GeoApp)
                ):
                    verb = action.get("updated_verb")
                    raw_action = "updated"

            if created is None:
                # object was deleted.
                verb = action.get("deleted_verb")
                raw_action = "deleted"
                action.update(action_object=None, target=None)
    except Exception as e:
        logger.exception(e)

    if verb:
        try:
            activity.send(
                action.get("actor"),
                verb=str(verb),
                action_object=action.get("action_object"),
                target=action.get("target", None),
                object_name=action.get("object_name"),
                raw_action=raw_action,
            )
        # except ModelNotActionable:
        except Exception:
            logger.warning("The activity received a non-actionable Model or None as the actor/action.")


def relationship_post_save_actstream(instance, sender, created, **kwargs):
    follow(instance.from_user, instance.to_user)


def relationship_pre_delete_actstream(instance, sender, **kwargs):
    unfollow(instance.from_user, instance.to_user)


def relationship_post_save(instance, sender, created, **kwargs):
    queue_notification([instance.to_user], "user_follow", {"from_user": instance.from_user})


if activity:
    signals.post_save.connect(activity_post_modify_object, sender=Dataset)
    signals.post_delete.connect(activity_post_modify_object, sender=Dataset)

    signals.post_save.connect(activity_post_modify_object, sender=Map)
    signals.post_delete.connect(activity_post_modify_object, sender=Map)

    signals.post_save.connect(activity_post_modify_object, sender=Document)
    signals.post_delete.connect(activity_post_modify_object, sender=Document)
    signals.post_save.connect(activity_post_modify_object, sender=GeoApp)
    signals.post_delete.connect(activity_post_modify_object, sender=GeoApp)


def rating_post_save(instance, sender, created, **kwargs):
    """Send a notification when rating a layer, map or document"""
    notice_type_label = f"{instance.content_object.class_name.lower()}_rated"
    recipients = get_notification_recipients(notice_type_label, instance.user, resource=instance.content_object)
    send_notification(
        recipients,
        notice_type_label,
        {"resource": instance.content_object, "user": instance.user, "rating": instance.rating},
    )

def user_wants_conversion():
    # Aqui você pode interagir com o front-end, por exemplo, usando um formulário ou uma mensagem
    # Temporariamente, pode retornar True ou False
    return True  # Assumindo que o usuário sempre deseja, como exemplo

def convert_to_epsg(layer_instance, target_epsg):
    from osgeo import ogr, osr
    
    # Acesse o arquivo do dataset
    filepath = layer_instance.filepath

    # Abra o dataset com OGR
    source = ogr.Open(filepath)
    source_layer = source.GetLayer()

    # Defina o sistema de coordenadas de destino
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(int(target_epsg))

    # Aqui você implementa a lógica de reprojeção e salva o dataset reprojetado
    # Atualize o caminho no GeoNode depois que o dataset for salvo.

def move_to_workspace(layer_instance, workspace_name):
    from geoserver.catalog import Catalog
    
    cat = Catalog("http://localhost:8080/geoserver/rest", "admin", "geoserver_password")
    layer = cat.get_layer(layer_instance.name)
    workspace = cat.get_workspace(workspace_name)
    if workspace:
        layer.workspace = workspace
        cat.save(layer)
    else:
        logger.error(f"Workspace {workspace_name} não encontrado.")