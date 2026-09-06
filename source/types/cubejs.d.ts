declare module "cubejs" {
  export interface CubeState {
    center: number[];
    cp: number[];
    co: number[];
    ep: number[];
    eo: number[];
  }

  export default class Cube {
    static initSolver(): void;
    static fromString(value: string): Cube;
    constructor(state?: CubeState);
    move(algorithm: string): this;
    solve(maxDepth?: number): string;
    asString(): string;
    isSolved(): boolean;
    toJSON(): CubeState;
  }
}
