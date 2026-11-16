// d3-force-3d 没有自带类型声明：只声明星图用到的部分
declare module 'd3-force-3d' {
  export interface SimulationNode {
    index?: number
    x?: number
    y?: number
    z?: number
    vx?: number
    vy?: number
    vz?: number
    fx?: number | null
    fy?: number | null
    fz?: number | null
  }
  export interface Force<N> {
    (alpha: number): void
    initialize?(nodes: N[], ...args: unknown[]): void
  }
  export interface Simulation<N> {
    tick(iterations?: number): this
    stop(): this
    restart(): this
    alpha(): number
    alpha(value: number): this
    alphaMin(): number
    alphaDecay(value: number): this
    velocityDecay(value: number): this
    nodes(): N[]
    force(name: string, force: unknown): this
  }
  export function forceSimulation<N extends SimulationNode>(nodes?: N[], numDimensions?: number): Simulation<N>
  export interface LinkForce<N, L> extends Force<N> {
    id(fn: (node: N) => string): this
    distance(value: number | ((link: L) => number)): this
    strength(value: number | ((link: L) => number)): this
  }
  export function forceLink<N, L>(links?: L[]): LinkForce<N, L>
  export interface ManyBody<N> extends Force<N> {
    strength(value: number | ((node: N) => number)): this
    distanceMax(value: number): this
  }
  export function forceManyBody<N>(): ManyBody<N>
  export interface Positional<N> extends Force<N> {
    strength(value: number | ((node: N) => number)): this
  }
  export function forceX<N>(x?: number | ((node: N) => number)): Positional<N>
  export function forceY<N>(y?: number | ((node: N) => number)): Positional<N>
  export function forceZ<N>(z?: number | ((node: N) => number)): Positional<N>
  export interface Collide<N> extends Force<N> {
    strength(value: number): this
  }
  export function forceCollide<N>(radius?: number | ((node: N) => number)): Collide<N>
}
