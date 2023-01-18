# Generated by Django 4.1.5 on 2023-01-16 20:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0039_rename_template_files_file'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='rental',
            name='renter',
        ),
        migrations.AlterField(
            model_name='profile',
            name='newsletter',
            field=models.BooleanField(blank=True, default=False, verbose_name='newsletter signup'),
        ),
    ]
