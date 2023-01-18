from django.contrib.auth.models import User, Group, Permission
from rest_framework import serializers
from rest_framework.exceptions import ParseError
from rest_framework.request import Request
from rest_framework import validators
from django.db import transaction
import logging
import re
from base.models import Category, RentalObject, RentalObjectType, Reservation, Rental, Tag, ObjectTypeInfo, Text, Profile
from base import models
from datetime import timedelta, datetime

logger = logging.getLogger(name="django")


class RentalObjectTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentalObjectType
        fields = '__all__'


class MaxRentDurationSerializer(serializers.ModelSerializer):
    duration_in_days = serializers.SerializerMethodField(
        'get_duration_in_days', required=False)

    class Meta:
        model = models.MaxRentDuration
        fields = '__all__'

    def get_duration_in_days(self, obj):
        """
        since parsing of the timedelta is tidious we add another field with the timedelta in days
        """
        return int(obj.duration.days)

    def create(self, validated_data):
        validated_data['duration'] = timedelta(
            days=validated_data['duration'].total_seconds())
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data['duration'] = timedelta(
            days=validated_data['duration'].total_seconds())
        return super().update(instance, validated_data)


class PrioritySerializer(serializers.ModelSerializer):

    class Meta:
        model = models.Priority
        fields = '__all__'


class ProfileSerializer(serializers.ModelSerializer):
    prio = PrioritySerializer(required=False)

    class Meta:
        model = Profile
        fields = '__all__'


class UserCreationSerializer(serializers.HyperlinkedModelSerializer):
    """
    Used for user registration 
    """
    profile = ProfileSerializer(required=False)

    class Meta:
        model = User
        fields = ['url', 'username', 'password', 'email',
                  'groups', 'id', 'first_name', 'last_name', 'profile']

    def validate_email(self, email):
        """
        overwrite the email validation to prevent multiuse of emails. Validate Email corresponding to a specific regex
        """
        regex = re.compile(models.Settings.objects.get(
            type='email_validation_regex').value)
        result = regex.fullmatch(email)
        if not (result and result.group(0) == email):
            raise serializers.ValidationError("Email ist im falsche Format")
        if User.objects.all().filter(email=email).count() > 0:
            raise serializers.ValidationError("Email bereits in Benutzung")
        return email

    @transaction.atomic
    def create(self, validated_data):
        """
        creates the user object in db and disables the login. also creates a profile with the supllied data. profile MUST be set. 
        """
        validated_data['is_active'] = False
        if 'groups' in validated_data:
            del validated_data['groups']
        profile_data = validated_data.pop('profile')
        user = User.objects.create_user(**validated_data)
        profile_data['user'] = user.pk
        profile_serializer = ProfileSerializer(data=profile_data)
        profile_serializer.is_valid(raise_exception=True)
        profile_serializer.save()
        return user


class UserSerializer(serializers.HyperlinkedModelSerializer):
    profile = ProfileSerializer()

    class Meta:
        model = User
        #fields = '__all__'
        fields = ['url', 'username', 'email',
                  'groups', 'id', 'first_name', 'last_name', 'profile']

    def create(self, validated_data):
        profile_data = validated_data.pop('profile')
        user = User.objects.create(**validated_data)
        Profile.objects.create(user=user, **profile_data)
        return user

    def update(self, instance: User, validated_data: dict):
        instance.username = validated_data.get('username', instance.username)
        instance.email = validated_data.get('email', instance.email)
        instance.save()
        # Profile is not neccessarily needed to update User
        try:
            profile_data = validated_data.pop('profile')
            profile = instance.profile
        except Profile.DoesNotExist:
            return instance
        except KeyError:
            return instance

        ProfileSerializer.update(profile, profile, profile_data)

        return instance

class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model= User
        exclude=['password']


class KnowLoginUserSerializer(serializers.ModelSerializer):
    user_permissions = serializers.SerializerMethodField(
        'get_user_permissions_name')

    profile = ProfileSerializer(read_only=True)
    class Meta:
        model = User
        fields = ['username', 'email', 'groups',
                  'is_staff', 'is_superuser', 'user_permissions', 'profile']

    def get_user_permissions_name(self, obj):
        # replace ids with Permission names to reduce the number of requests
        return obj.get_all_permissions()


class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ['url', 'name']


class RentalObjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentalObject
        fields = '__all__'


class CategorySerializer(serializers.ModelSerializer):

    class Meta:
        model = Category
        fields = ('name', 'description', 'id')


class BulkReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Reservation
        fields = '__all__'
        validators = [
            validators.UniqueTogetherValidator(
                queryset=models.Reservation.objects.all(),
                fields=['reserver', 'operation_number', 'objecttype'],
                message="This Combination of reserver, operation_number and object_type already exists"
            ),
        ]

    def validate(self, data):
        if models.RentalObjectType.max_rent_duration(pk=data['objecttype'].pk, prio=data['reserver'].prio).duration + timedelta(days=7) < data['reserved_until']-data['reserved_from']:
            raise serializers.ValidationError(
                detail="the rent duration exceeds max_rent_duration.")
        if data['reserved_from'].isoweekday() != int(models.Settings.objects.get(type='lenting_day').value):
            raise serializers.ValidationError(
                detail="this day is not a lenting day therefore a reservation can not start here")
        if data['reserved_until'].isoweekday() != int(models.Settings.objects.get(type='returning_day').value):
            raise serializers.ValidationError(
                detail="this day is not a returning day therefore a reservation can not end here")
        if data['reserved_from'] >= data['reserved_until']:
            raise serializers.ValidationError(
                detail="reserved_from must be before reserved_until")
        if data['count'] > models.RentalObjectType.available(pk=data['objecttype'], from_date=data['reserved_from'], until_date=data['reserved_until'])['available']:
            raise serializers.ValidationError(
                detail="There are not enough objects of this type to fullfill your reservation")
        return data

class ReservationProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = models.Profile
        fields = '__all__'

class ReservationSerializer(serializers.ModelSerializer):
    reserver = ReservationProfileSerializer(read_only=True)
    objecttype = RentalObjectTypeSerializer(read_only=True)
    class Meta:
        model = models.Reservation
        fields = '__all__'


class RentalSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        """
        do not tell the renting person the person who rented them the object
        """
        request: Request = kwargs.get(
            'context', {}).get('request')  # type: ignore
        super(RentalSerializer, self).__init__(*args, **kwargs)
        if request == None or not request.user.is_staff:
            self.fields.pop('return_processor')
            self.fields.pop('lender')
    def create(self, validated_data):
        logger.info(validated_data)
        validated_data['reservation'] = validated_data['reservation']
        rental = models.Rental.objects.create(**validated_data)
        return rental

    reservation = ReservationSerializer(required=False)

    class Meta:
        model = Rental
        fields = '__all__'

class RentalCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rental
        fields = '__all__'


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = '__all__'


class ObjectTypeInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ObjectTypeInfo
        fields = '__all__'


class TextSerializer(serializers.ModelSerializer):
    class Meta:
        model = Text
        fields = '__all__'


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Settings
        fields = ['type', 'value', 'id']

class FilesSerializer(serializers.ModelSerializer):
    class Meta:
        model=models.Files
        fields = '__all__'
