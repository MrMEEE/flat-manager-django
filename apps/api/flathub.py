from datetime import date

from django.db.models import Count, Q
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.flatpak.models import AppMetadata, AppPickSelection, AppPickTheme, AppUsageObservation, ModerationRequest, Package, QualityModerationResult
from .permissions import IsAdmin


class PublishedPackageQuery:
    """Shared query and response helpers for the public Flathub API surface."""

    @staticmethod
    def queryset():
        return Package.objects.filter(status='published').select_related('repository', 'app_metadata')

    @staticmethod
    def json_data(package):
        metadata = getattr(package, 'app_metadata', None)
        return {
            'id': package.package_id,
            'name': package.package_name,
            'summary': metadata.summary if metadata and metadata.summary else package.package_name,
            'description': metadata.description if metadata else '',
            'homepage': metadata.homepage if metadata else '',
            'icon_url': metadata.icon_url if metadata else '',
            'screenshots': metadata.screenshots if metadata else [],
            'developer': metadata.developer_name if metadata else '',
            'categories': metadata.categories if metadata else [],
            'keywords': metadata.keywords if metadata else [],
            'verified': metadata.is_verified if metadata else False,
            'mobile': metadata.is_mobile if metadata else False,
            'version': package.version,
            'branch': package.branch,
            'arch': package.arch,
            'repository': package.repository.name,
            'updated_at': package.updated_at.isoformat(),
        }


class StatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok'})


class AppstreamView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id=None):
        packages = PublishedPackageQuery.queryset()
        if app_id:
            package = packages.filter(package_id=app_id).first()
            if package is None:
                return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response(PublishedPackageQuery.json_data(package))

        sort = request.query_params.get('sort', 'alphabetical')
        if sort in ('recent', 'recently-updated'):
            packages = packages.order_by('-updated_at')
        else:
            packages = packages.order_by('package_id')
        return Response([PublishedPackageQuery.json_data(package) for package in packages])


class SummaryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id):
        package = PublishedPackageQuery.queryset().filter(package_id=app_id).first()
        if package is None:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'id': package.package_id,
            'name': package.package_name,
            'version': package.version,
            'branch': package.branch,
            'arch': package.arch,
            'status': package.status,
        })


class AppStatsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id=None):
        packages = PublishedPackageQuery.queryset()
        if app_id:
            package = packages.filter(package_id=app_id).first()
            if package is None:
                return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response({
                'app_id': package.package_id,
                'builds': package.builds.count(),
                'build_number': package.build_number,
                'version': package.version,
                'updated_at': package.updated_at.isoformat(),
                'usage': usage_for_app(package.package_id),
            })
        return Response({
            'apps': packages.count(),
            'repositories': packages.values('repository_id').distinct().count(),
        })


def usage_for_app(app_id, since=None):
    observations = AppUsageObservation.objects.filter(app_id=app_id)
    if since:
        observations = observations.filter(observed_at__gte=since)
    return {
        'observations': observations.count(),
        'unique_clients': observations.values('client_id').distinct().count(),
    }


class UsageView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        days = int(request.query_params.get('days', 30))
        days = max(1, min(days, 365))
        since = timezone.now() - timedelta(days=days)
        usage = AppUsageObservation.objects.filter(
            observed_at__gte=since,
            app_id__in=PublishedPackageQuery.queryset().values('package_id'),
        ).values('app_id').annotate(
            observations=Count('id'),
            unique_clients=Count('client_id', distinct=True),
        ).order_by('-unique_clients', '-observations', 'app_id')
        return Response({
            'days': days,
            'apps': list(usage.values('app_id', 'unique_clients', 'observations')),
        })


class AddonView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id):
        if not PublishedPackageQuery.queryset().filter(package_id=app_id).exists():
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response([])


class PlatformView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(sorted(set(PublishedPackageQuery.queryset().values_list('arch', flat=True))))


class RuntimeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        runtimes = []
        for package in PublishedPackageQuery.queryset():
            dependencies = package.dependencies or {}
            runtime = dependencies.get('runtime') if isinstance(dependencies, dict) else None
            if runtime and runtime not in runtimes:
                runtimes.append(runtime)
        return Response(sorted(runtimes))


class FullscreenAppView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id):
        exists = PublishedPackageQuery.queryset().filter(package_id=app_id).exists()
        if not exists:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(False)


class SearchView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        query = request.data.get('query', request.data.get('search', ''))
        if not isinstance(query, str) or not query.strip():
            return Response({'detail': 'query is required'}, status=status.HTTP_400_BAD_REQUEST)

        query = query.strip()
        packages = PublishedPackageQuery.queryset().filter(
            Q(package_id__icontains=query)
            | Q(package_name__icontains=query)
            | Q(app_metadata__summary__icontains=query)
            | Q(app_metadata__developer_name__icontains=query)
        ).order_by('package_id')
        return Response([PublishedPackageQuery.json_data(package) for package in packages])


class PackageCollectionView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, collection):
        packages = PublishedPackageQuery.queryset()
        if collection in ('popular', 'trending'):
            since = timezone.now() - timedelta(days=30)
            usage = AppUsageObservation.objects.filter(
                observed_at__gte=since,
                app_id__in=packages.values('package_id'),
            ).values('app_id').annotate(
                unique_clients=Count('client_id', distinct=True),
                observations=Count('id'),
            ).order_by('-unique_clients', '-observations', 'app_id')
            usage_by_app = {item['app_id']: item for item in usage}
            packages = sorted(
                packages,
                key=lambda package: (
                    -usage_by_app.get(package.package_id, {}).get('unique_clients', 0),
                    -usage_by_app.get(package.package_id, {}).get('observations', 0),
                    package.package_id,
                ),
            )
        elif collection in ('recently-added', 'new'):
            packages = packages.order_by('-created_at')
        elif collection == 'recently-updated':
            packages = packages.order_by('-updated_at')
        elif collection in ('verified', 'mobile', 'alphabetical'):
            if collection == 'verified':
                packages = packages.filter(app_metadata__is_verified=True)
            elif collection == 'mobile':
                packages = packages.filter(app_metadata__is_mobile=True)
            packages = packages.order_by('package_id')
        else:
            return Response({'detail': 'Collection not found'}, status=status.HTTP_404_NOT_FOUND)
        if hasattr(packages, 'order_by'):
            packages = packages.order_by('package_id') if collection not in ('popular', 'trending') else packages
        return Response([PublishedPackageQuery.json_data(package) for package in packages])


class FeedView(PackageCollectionView):
    def get(self, request, feed):
        if feed not in ('new', 'recently-updated'):
            return Response({'detail': 'Feed not found'}, status=status.HTTP_404_NOT_FOUND)
        return super().get(request, 'recently-added' if feed == 'new' else feed)


class NamedCollectionView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, collection, value=None):
        packages = PublishedPackageQuery.queryset()
        if collection == 'category':
            if value:
                packages = [
                    package for package in packages
                    if value in (getattr(package, 'app_metadata', None).categories
                                 if getattr(package, 'app_metadata', None) else [])
                ]
            else:
                categories = set()
                for package in packages:
                    metadata = getattr(package, 'app_metadata', None)
                    categories.update(metadata.categories if metadata else [])
                return Response(sorted(categories))
        elif collection == 'developer':
            if value:
                packages = packages.filter(app_metadata__developer_name__iexact=value)
            else:
                return Response(sorted(set(
                    packages.exclude(app_metadata__developer_name='').values_list(
                        'app_metadata__developer_name', flat=True
                    )
                )))
        elif collection in ('keyword', 'keywords'):
            keywords = set()
            for package in packages:
                metadata = getattr(package, 'app_metadata', None)
                keywords.update(metadata.keywords if metadata else [])
            return Response(sorted(keywords))
        else:
            return Response({'detail': 'Collection not found'}, status=status.HTTP_404_NOT_FOUND)
        if hasattr(packages, 'order_by'):
            packages = packages.order_by('package_id')
        else:
            packages = sorted(packages, key=lambda package: package.package_id)
        return Response([PublishedPackageQuery.json_data(package) for package in packages])


class AppPickAdminListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        selections = AppPickSelection.objects.select_related('package').order_by('-date', 'rank')
        return Response([app_pick_data(selection) for selection in selections])


class AppPickThemeView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([
            {'id': theme.id, 'name': theme.name, 'description': theme.description}
            for theme in AppPickTheme.objects.all()
        ])


class AppMetadataView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, app_id):
        package = Package.objects.filter(package_id=app_id).first()
        if package is None:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        metadata, _ = AppMetadata.objects.get_or_create(package=package)
        for field in (
            'summary', 'description', 'homepage', 'icon_url', 'screenshots',
            'developer_name', 'categories', 'keywords', 'is_verified', 'is_mobile',
        ):
            if field in request.data:
                setattr(metadata, field, request.data[field])
        metadata.save()
        return Response(PublishedPackageQuery.json_data(package))


class QualityStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        packages = PublishedPackageQuery.queryset()
        results = QualityModerationResult.objects.filter(package__in=packages)
        return Response({
            'total_apps': packages.count(),
            'passing_apps': results.filter(status='passing').count(),
            'failed_apps': results.filter(status='failed').count(),
            'pending_apps': packages.count() - results.filter(status__in=['passing', 'failed']).count(),
        })


class AppPickSelectionDetailView(APIView):
    permission_classes = [IsAdmin]

    def get_object(self, selection_id):
        return AppPickSelection.objects.filter(pk=selection_id).select_related('package').first()

    def put(self, request, selection_id):
        selection = self.get_object(selection_id)
        if selection is None:
            return Response({'detail': 'App pick not found'}, status=status.HTTP_404_NOT_FOUND)
        for field in ('title', 'description', 'rank', 'kind'):
            if field in request.data:
                setattr(selection, field, request.data[field])
        if 'date' in request.data:
            try:
                selection.date = date.fromisoformat(request.data['date'])
            except (TypeError, ValueError):
                return Response({'detail': 'date must be an ISO date'}, status=status.HTTP_400_BAD_REQUEST)
        selection.save()
        return Response(app_pick_data(selection))

    def delete(self, request, selection_id):
        selection = self.get_object(selection_id)
        if selection is None:
            return Response({'detail': 'App pick not found'}, status=status.HTTP_404_NOT_FOUND)
        selection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CategorySubcategoriesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, category):
        return Response([])


class QualityAppsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, result):
        packages = PublishedPackageQuery.queryset()
        if result == 'passing-apps':
            packages = packages.filter(quality_moderation__status='passing')
            return Response([PublishedPackageQuery.json_data(package) for package in packages])
        if result == 'failed-by-guideline':
            results = QualityModerationResult.objects.filter(
                package__in=packages, status='failed'
            ).select_related('package')
            return Response([{
                'app_id': result.package.package_id,
                'guidelines': result.guidelines,
                'comment': result.review_comment,
            } for result in results])
        if result == 'app-pick-recommendations':
            return Response([])
        return Response({'detail': 'Quality report not found'}, status=status.HTTP_404_NOT_FOUND)


class QualityAppView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, app_id):
        package = PublishedPackageQuery.queryset().filter(package_id=app_id).first()
        if package is None:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        result, _ = QualityModerationResult.objects.get_or_create(package=package)
        return Response({
            'id': package.package_id,
            'status': result.status,
            'fullscreen': result.fullscreen,
            'guidelines': result.guidelines,
            'review_comment': result.review_comment,
        })


class QualityAppActionView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, app_id, action):
        package = Package.objects.filter(package_id=app_id).first()
        if package is None:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        if action == 'request-review':
            moderation = ModerationRequest.objects.create(
                package=package, submitted_by=request.user,
                reason=request.data.get('reason', 'Quality review requested'),
            )
            return Response({'id': moderation.id, 'status': moderation.status}, status=status.HTTP_201_CREATED)
        result, _ = QualityModerationResult.objects.get_or_create(package=package)
        result.fullscreen = True
        result.reviewed_by = request.user
        result.reviewed_at = timezone.now()
        result.save(update_fields=['fullscreen', 'reviewed_by', 'reviewed_at', 'updated_at'])
        return Response({'app_id': app_id, 'fullscreen': result.fullscreen})

    def delete(self, request, app_id, action):
        if action != 'request-review':
            return Response({'detail': 'Action not found'}, status=status.HTTP_404_NOT_FOUND)
        ModerationRequest.objects.filter(
            package__package_id=app_id, status='pending'
        ).update(status='rejected', reviewed_by=request.user, reviewed_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


class QualityStatsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = PublishedPackageQuery.queryset().values('branch').annotate(count=Count('id'))
        return Response({item['branch']: item['count'] for item in categories})


class YearInReviewView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, year):
        packages = PublishedPackageQuery.queryset().filter(created_at__year=year)
        observations = AppUsageObservation.objects.filter(
            app_id__in=packages.values('package_id'), observed_at__year=year,
        )
        return Response({
            'year': year,
            'apps_added': packages.count(),
            'apps_updated': PublishedPackageQuery.queryset().filter(updated_at__year=year).count(),
            'usage_observations': observations.count(),
            'unique_clients': observations.values('client_id').distinct().count(),
            'generated_at': timezone.now().isoformat(),
        })


def app_pick_data(selection):
    return {
        'id': selection.id,
        'app_id': selection.package.package_id,
        'name': selection.package.package_name,
        'kind': selection.kind,
        'date': selection.date.isoformat(),
        'title': selection.title,
        'description': selection.description,
        'rank': selection.rank,
    }


class AppPickView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, kind=None, date=None):
        selections = AppPickSelection.objects.filter(package__status='published').select_related('package')
        if kind:
            selections = selections.filter(kind=kind)
        if date:
            selections = selections.filter(date=date)
        return Response([app_pick_data(selection) for selection in selections])


class AppPickDateView(AppPickView):
    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAdmin()]
        return [AllowAny()]

    def get(self, request, kind=None, date=None):
        return super().get(request, kind=kind, date=date or timezone.localdate().isoformat())

    def post(self, request, kind=None):
        package = Package.objects.filter(
            package_id=request.data.get('app_id'), status='published'
        ).first()
        if package is None:
            return Response({'detail': 'Published application not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            selection = AppPickSelection.objects.create(
                package=package,
                kind=kind,
                date=date.fromisoformat(request.data.get('date', timezone.localdate().isoformat())),
                title=request.data.get('title', ''),
                description=request.data.get('description', ''),
                rank=request.data.get('rank', 0),
                created_by=request.user,
            )
        except (TypeError, ValueError):
            return Response({'detail': 'date must be an ISO date'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(app_pick_data(selection), status=status.HTTP_201_CREATED)


class AdminAppPickView(AppPickView):
    permission_classes = [IsAdmin]


class AppPickAdminView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        selections = AppPickSelection.objects.select_related('package').order_by('-date', 'rank')
        return Response([app_pick_data(selection) for selection in selections])

    def post(self, request):
        package = Package.objects.filter(
            package_id=request.data.get('app_id'), status='published'
        ).first()
        if package is None:
            return Response({'detail': 'Published application not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            selection_date = date.fromisoformat(request.data['date'])
            selection = AppPickSelection.objects.create(
                package=package,
                kind=request.data['kind'],
                date=selection_date,
                title=request.data.get('title', ''),
                description=request.data.get('description', ''),
                rank=request.data.get('rank', 0),
                created_by=request.user,
            )
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'kind and date are required'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(app_pick_data(selection), status=status.HTTP_201_CREATED)


class ModerationAppsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, app_id=None):
        requests = ModerationRequest.objects.select_related('package', 'submitted_by', 'reviewed_by')
        if app_id:
            requests = requests.filter(package__package_id=app_id)
        return Response([{
            'id': item.id,
            'app_id': item.package.package_id,
            'status': item.status,
            'reason': item.reason,
            'review_comment': item.review_comment,
            'created_at': item.created_at.isoformat(),
            'reviewed_at': item.reviewed_at.isoformat() if item.reviewed_at else None,
        } for item in requests])


class SubmitModerationView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        package = Package.objects.filter(package_id=request.data.get('app_id')).first()
        if package is None:
            return Response({'detail': 'Application not found'}, status=status.HTTP_404_NOT_FOUND)
        moderation = ModerationRequest.objects.create(
            package=package,
            submitted_by=request.user,
            reason=request.data.get('reason', ''),
        )
        return Response({'id': moderation.id, 'status': moderation.status}, status=status.HTTP_201_CREATED)


class ReviewModerationView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, request_id):
        moderation = ModerationRequest.objects.filter(pk=request_id).first()
        if moderation is None:
            return Response({'detail': 'Moderation request not found'}, status=status.HTTP_404_NOT_FOUND)
        decision = request.data.get('status')
        if decision not in ('approved', 'rejected'):
            return Response({'detail': 'status must be approved or rejected'}, status=status.HTTP_400_BAD_REQUEST)
        moderation.status = decision
        moderation.review_comment = request.data.get('comment', '')
        moderation.reviewed_by = request.user
        moderation.reviewed_at = timezone.now()
        moderation.save(update_fields=['status', 'review_comment', 'reviewed_by', 'reviewed_at'])
        return Response({'id': moderation.id, 'status': moderation.status})