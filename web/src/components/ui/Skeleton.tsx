interface SkeletonProps {
  className?: string
}

/**
 * Base skeleton component for loading states
 */
export function Skeleton({ className = '' }: SkeletonProps) {
  return (
    <div
      className={`animate-pulse bg-surface-700 rounded ${className}`}
      aria-hidden="true"
    />
  )
}

/**
 * Card skeleton for dashboard stats
 */
export function SkeletonCard() {
  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 p-4">
      <Skeleton className="h-4 w-20 mb-2" />
      <Skeleton className="h-8 w-16" />
    </div>
  )
}

/**
 * Table row skeleton for job lists
 */
export function SkeletonTableRow() {
  return (
    <tr className="border-b border-surface-700">
      <td className="px-4 py-3"><Skeleton className="h-4 w-8" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-48" /></td>
      <td className="px-4 py-3"><Skeleton className="h-5 w-20 rounded-full" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-32" /></td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-8" /></td>
      <td className="px-4 py-3"><Skeleton className="h-6 w-20" /></td>
    </tr>
  )
}

/**
 * List item skeleton for projects
 */
export function SkeletonListItem() {
  return (
    <div className="p-4 bg-surface-800 border border-surface-700 rounded-lg">
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <Skeleton className="h-5 w-48 mb-2" />
          <Skeleton className="h-4 w-32" />
        </div>
        <div className="text-right">
          <Skeleton className="h-4 w-16 mb-1" />
          <Skeleton className="h-3 w-12" />
        </div>
      </div>
    </div>
  )
}

/**
 * Dashboard skeleton for full loading state
 */
export function SkeletonDashboard() {
  return (
    <div className="space-y-6" aria-label="Loading dashboard" role="status">
      <Skeleton className="h-8 w-32" />

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>

      {/* Recent Jobs */}
      <div className="bg-surface-800 rounded-lg border border-surface-700">
        <div className="px-4 py-3 border-b border-surface-700 flex items-center justify-between">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-4 w-16" />
        </div>
        <div className="divide-y divide-surface-700">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="px-4 py-3">
              <div className="flex items-center justify-between">
                <div>
                  <Skeleton className="h-5 w-48 mb-2" />
                  <Skeleton className="h-4 w-32" />
                </div>
                <Skeleton className="h-5 w-20" />
              </div>
            </div>
          ))}
        </div>
      </div>

      <span className="sr-only">Loading dashboard content...</span>
    </div>
  )
}

/**
 * Queue skeleton for job list loading state
 */
export function SkeletonQueue() {
  return (
    <div className="space-y-6" aria-label="Loading queue" role="status">
      <div className="flex justify-between items-center">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-10 w-64" />
      </div>

      <Skeleton className="h-10 w-96" />

      <div className="bg-surface-800 rounded-lg border border-surface-700 overflow-hidden">
        <table className="w-full">
          <thead className="bg-surface-850 border-b border-surface-700">
            <tr>
              <th className="px-4 py-3"><Skeleton className="h-4 w-8" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-20" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-16" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-12" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-20" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-16" /></th>
              <th className="px-4 py-3"><Skeleton className="h-4 w-16" /></th>
            </tr>
          </thead>
          <tbody>
            {[1, 2, 3, 4, 5].map((i) => (
              <SkeletonTableRow key={i} />
            ))}
          </tbody>
        </table>
      </div>

      <span className="sr-only">Loading queue content...</span>
    </div>
  )
}

export default Skeleton
